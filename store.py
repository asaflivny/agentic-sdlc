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
)
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
)
"""


class WorkflowStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def setup(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_FINDINGS_TABLE)
            await db.commit()
        logger.info("store ready db=%s", self.db_path)

    async def save(self, run_id: str, result: WorkflowResult) -> None:
        total = sum(len(r.findings) for r in result.agent_results)
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
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
                        await db.execute(
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
                await db.commit()
            logger.info("saved run_id=%s total_findings=%d", run_id, total)
        except Exception:
            logger.exception("failed to save run_id=%s", run_id)

    async def list_runs(
        self,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list = []
        if repo:
            conditions.append("repo = ?")
            params.append(repo)
        if branch:
            conditions.append("branch = ?")
            params.append(branch)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(max(1, min(limit, 200)))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT run_id, workflow, repo, branch, started_at, completed_at, findings, "
                f"ROUND((julianday(completed_at) - julianday(started_at)) * 86400, 1) AS duration_seconds "
                f"FROM workflow_runs {where} ORDER BY id DESC LIMIT ?",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_run(self, run_id: str) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT result_json FROM workflow_runs WHERE run_id = ? LIMIT 1",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])
