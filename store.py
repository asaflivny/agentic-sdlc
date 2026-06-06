import json
import logging
from typing import Optional

import aiosqlite

from models.results import WorkflowResult

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL UNIQUE,
    workflow    TEXT NOT NULL,
    repo        TEXT NOT NULL,
    branch      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    findings    INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_run_id ON workflow_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_completed_at ON workflow_runs(completed_at);
"""

_CREATE_FINDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    agent       TEXT NOT NULL,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT,
    recommendation TEXT,
    file_path   TEXT,
    line_number INTEGER,
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_findings_run_id ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
"""


class WorkflowStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    async def setup(self) -> None:
        """Initialize database connection and create tables with indices."""
        self.conn = await aiosqlite.connect(self.db_path)
        # Execute each statement separately (aiosqlite can only execute one at a time)
        for statement in _CREATE_TABLE.strip().split(';'):
            statement = statement.strip()
            if statement:
                await self.conn.execute(statement)
        for statement in _CREATE_FINDINGS_TABLE.strip().split(';'):
            statement = statement.strip()
            if statement:
                await self.conn.execute(statement)
        await self.conn.commit()
        logger.info("store ready db=%s (persistent connection pooling enabled)", self.db_path)

    async def cleanup(self) -> None:
        """Close the persistent database connection."""
        if self.conn:
            await self.conn.close()
            logger.info("store connection closed")

    async def save(self, run_id: str, result: WorkflowResult) -> None:
        if not self.conn:
            logger.error("store not initialized, cannot save run_id=%s", run_id)
            return
        total = sum(len(r.findings) for r in result.agent_results)
        try:
            await self.conn.execute(
                """INSERT OR REPLACE INTO workflow_runs
                   (run_id, workflow, repo, branch, started_at, completed_at, findings, result_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    result.workflow_name,
                    result.repo_name,
                    result.branch,
                    result.started_at.isoformat(),
                    result.completed_at.isoformat(),
                    total,
                    result.model_dump_json(),
                ),
            )
            for agent_result in result.agent_results:
                for f in agent_result.findings:
                    await self.conn.execute(
                        """INSERT INTO findings
                           (run_id, agent, severity, title, description, recommendation, file_path, line_number)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            run_id,
                            agent_result.agent_name,
                            f.severity,
                            f.title,
                            f.description,
                            f.recommendation,
                            f.file_path,
                            f.line_number,
                        ),
                    )
            await self.conn.commit()
            logger.info("saved run_id=%s total_findings=%d", run_id, total)
        except Exception:
            logger.exception("failed to save run_id=%s", run_id)

    async def list_runs(
        self,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        if not self.conn:
            return []
        conditions: list[str] = []
        params: list = []
        if repo:
            conditions.append("wr.repo = ?")
            params.append(repo)
        if branch:
            conditions.append("wr.branch = ?")
            params.append(branch)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(max(1, min(limit, 200)))
        self.conn.row_factory = aiosqlite.Row
        async with self.conn.execute(
            f"""SELECT wr.run_id, wr.workflow, wr.repo, wr.branch,
                wr.started_at, wr.completed_at, wr.findings,
                ROUND((julianday(wr.completed_at) - julianday(wr.started_at)) * 86400, 1) AS duration_seconds,
                SUM(CASE WHEN f.severity = 'critical' THEN 1 ELSE 0 END) AS critical_count,
                SUM(CASE WHEN f.severity = 'high'     THEN 1 ELSE 0 END) AS high_count,
                SUM(CASE WHEN f.severity = 'medium'   THEN 1 ELSE 0 END) AS medium_count,
                SUM(CASE WHEN f.severity = 'low'      THEN 1 ELSE 0 END) AS low_count,
                SUM(CASE WHEN f.severity = 'info'     THEN 1 ELSE 0 END) AS info_count
                FROM workflow_runs wr
                LEFT JOIN findings f ON wr.run_id = f.run_id
                {where}
                GROUP BY wr.id, wr.run_id, wr.workflow, wr.repo, wr.branch,
                         wr.started_at, wr.completed_at, wr.findings
                ORDER BY wr.id DESC LIMIT ?""",
            params,
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_stats(self) -> dict:
        if not self.conn:
            return {"total_runs": 0, "total_findings": 0, "avg_duration_seconds": None}
        self.conn.row_factory = aiosqlite.Row
        async with self.conn.execute(
            """SELECT COUNT(*) as total_runs,
               COALESCE(SUM(findings), 0) as total_findings,
               ROUND(AVG((julianday(completed_at) - julianday(started_at)) * 86400), 1) as avg_duration_seconds
               FROM workflow_runs"""
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return {"total_runs": 0, "total_findings": 0, "avg_duration_seconds": None}
        return dict(row)

    async def get_findings_trend(self, repo: str, days: int = 30) -> list[dict]:
        """Return daily finding counts by severity for *repo* over the last *days* days."""
        if not self.conn:
            return []
        days = max(1, min(days, 365))
        self.conn.row_factory = aiosqlite.Row
        async with self.conn.execute(
            """SELECT date(wr.started_at) AS date,
                      f.severity,
                      COUNT(*) AS count
               FROM findings f
               JOIN workflow_runs wr ON f.run_id = wr.run_id
               WHERE wr.repo = ?
                 AND wr.started_at >= datetime('now', ? || ' days')
               GROUP BY date(wr.started_at), f.severity
               ORDER BY date(wr.started_at), f.severity""",
            (repo, f"-{days}"),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_run(self, run_id: str) -> Optional[dict]:
        if not self.conn:
            return None
        self.conn.row_factory = aiosqlite.Row
        async with self.conn.execute(
            "SELECT result_json FROM workflow_runs WHERE run_id = ? LIMIT 1",
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])
