# PostgreSQL Migration Path

This document describes the strategy for migrating from SQLite to PostgreSQL for multi-instance deployments.

## Why Migrate?

- **SQLite limitations**: Single-writer, file-based, difficult for distributed setups
- **PostgreSQL benefits**: Multi-writer, network-accessible, ACID transactions, connection pooling, horizontal scaling
- **Use case**: When running asdlc across multiple instances (load-balanced, high availability)

## When to Migrate

Migrate to PostgreSQL when:
- Running more than ~5 concurrent asdlc instances
- Need high availability / fault tolerance
- Have 10,000+ workflow runs in SQLite
- Want centralized data for cross-instance analytics

## Migration Strategy

### Phase 1: Preparation

1. **Backup SQLite database**
   ```bash
   cp asdlc.db asdlc.db.backup
   ```

2. **Export data from SQLite**
   ```bash
   sqlite3 asdlc.db ".mode json" < export_queries.sql > asdlc_export.json
   ```

3. **Provision PostgreSQL**
   - Create new PostgreSQL database and user
   - Set connection string: `postgresql://user:pass@host:5432/asdlc`

### Phase 2: Create PostgreSQL Schema

```sql
-- Equivalent to SQLite schema
CREATE TABLE workflow_runs (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(255) NOT NULL UNIQUE,
    workflow VARCHAR(255) NOT NULL,
    repo VARCHAR(255) NOT NULL,
    branch VARCHAR(255) NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP NOT NULL,
    findings INTEGER NOT NULL DEFAULT 0,
    result_json JSONB NOT NULL
);

CREATE TABLE findings (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(255) NOT NULL REFERENCES workflow_runs(run_id),
    agent VARCHAR(255) NOT NULL,
    severity VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    recommendation TEXT,
    file_path VARCHAR(255),
    line_number INTEGER
);

-- Indices
CREATE INDEX idx_workflow_runs_run_id ON workflow_runs(run_id);
CREATE INDEX idx_workflow_runs_completed_at ON workflow_runs(completed_at);
CREATE INDEX idx_findings_run_id ON findings(run_id);
CREATE INDEX idx_findings_severity ON findings(severity);
CREATE INDEX idx_findings_agent ON findings(agent);
```

### Phase 3: Seed PostgreSQL with Existing Data

```bash
# Using pg_restore or psql
psql -U user -d asdlc < migration_script.sql
```

Migration script should:
1. Parse `asdlc_export.json`
2. INSERT workflow_runs rows
3. INSERT findings rows
4. Verify row counts match SQLite

### Phase 4: Update asdlc Configuration

```bash
# .env or environment variables
STORAGE_TYPE=postgresql  # future feature; currently hardcoded to sqlite
DATABASE_URL=postgresql://user:pass@host:5432/asdlc
```

### Phase 5: Code Changes (Future Implementation)

Currently, `store.py` uses `aiosqlite`. To support PostgreSQL:

1. **Create `storage/postgres_adapter.py`**
   - Implement same interface as `WorkflowStore`
   - Use `asyncpg` library for async PostgreSQL access
   - Connection pooling via `asyncpg.create_pool()`

2. **Factory pattern in `main.py`**
   ```python
   if settings.storage_type == "postgresql":
       from storage.postgres_adapter import PostgresWorkflowStore
       store = PostgresWorkflowStore(settings.database_url)
   else:
       from store import WorkflowStore
       store = WorkflowStore(settings.db_path)
   ```

3. **Test compatibility**
   - Run full test suite against both SQLite and PostgreSQL
   - Verify query results match

## Rollback Plan

If issues occur during migration:

1. **Keep SQLite operational** — don't delete `asdlc.db` until verified stable
2. **Revert environment** — switch `DATABASE_URL` back to SQLite path
3. **Restart asdlc** — new instances will use SQLite again
4. **Investigate PostgreSQL issues** — check logs for connection/permission errors

## Performance Expectations

Post-migration:

| Operation | SQLite | PostgreSQL |
|---|---|---|
| Query 1000 findings | ~200ms | ~50ms |
| Concurrent writes | Serialized | Parallel |
| Multi-instance support | ❌ | ✅ |
| Connection count | 1 | Pool (5-20) |

## Cost Estimation

- **PostgreSQL hosting**: ~$15-50/month (small instance on AWS RDS, Azure, DigitalOcean)
- **Migration effort**: 2-4 hours (testing included)
- **Ongoing maintenance**: Same as SQLite (schema changes tracked in migrations)

## Next Steps

1. Plan migration window (off-peak hours recommended)
2. Test with staging data first
3. Implement `storage/postgres_adapter.py`
4. Run full test suite
5. Perform production migration in phases (canary on one instance)

## References

- [asyncpg documentation](https://magicstack.github.io/asyncpg/)
- [PostgreSQL JSON support](https://www.postgresql.org/docs/current/datatype-json.html) for `result_json`
- [Connection pooling best practices](https://wiki.postgresql.org/wiki/Number_Of_Database_Connections)
