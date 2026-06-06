# Known Issues & Caveats

## Common Bugs

### Race Condition in User Creation

**Issue**: When creating users concurrently, duplicate entries can occur if checking existence and inserting are not atomic.

**Affected Code**: `user_service.py:create_user()`

**Fix**: Use database constraints (UNIQUE index) + INSERT OR IGNORE, or transaction with SELECT FOR UPDATE.

**Status**: In progress - #42

### Memory Leak in Connection Pool

**Issue**: Database connections not properly closed after request errors, slowly exhausting the pool.

**Root Cause**: Missing try-finally blocks in connection handling.

**Affected Service**: PaymentService

**Fix**: Ensure all connections use context managers (with statements).

**Status**: Fixed in v1.2.3

### Cache Invalidation Bug

**Issue**: Cache entries not invalidated when data is updated through admin panel.

**Root Cause**: Admin panel uses different code path that doesn't trigger cache invalidation hooks.

**Affected**: ProductCache

**Fix**: Implement event-driven cache invalidation or use cache versioning.

**Status**: Planned for v2.0

## Performance Issues

### Slow Dashboard Load

**Issue**: Dashboard takes 30+ seconds to load in production.

**Root Cause**: Unindexed queries joining 5 tables without pagination.

**Affected Code**: `dashboard_service.py:get_overview()`

**Workaround**: Apply query timeout, use caching, or split into smaller requests.

**Fix**: Add database indexes on foreign keys, implement pagination.

**Status**: Fixed in v1.3.0

### N+1 Query Problem in Report Generation

**Issue**: Report generation makes one query per record (should be one query for all).

**Affected Code**: `reports.py:generate_monthly_report()`

**Fix**: Use eager loading (JOIN) instead of lazy loading.

## Compatibility Issues

### PostgreSQL 12 Compatibility

**Issue**: Some queries use PostgreSQL 14+ syntax and fail on PostgreSQL 12.

**Workaround**: Upgrade PostgreSQL to 14+

**Affected Queries**: Window functions, generated columns

**Status**: Will document minimum version requirement

### Python 3.8 Support

**Issue**: Project uses Python 3.10+ syntax (match statements, union types with |).

**Workaround**: Upgrade to Python 3.10+

**Status**: Python 3.8 support discontinued

## Third-Party Service Issues

### Auth0 Outage Impact

**Issue**: Login fails completely when Auth0 is unreachable (no fallback).

**Mitigation**: Implement local session cache, allow grace period for service recovery.

**Status**: Documented in runbook, automatic retry in progress

### Stripe Integration Rate Limiting

**Issue**: Occasional 429 (Too Many Requests) from Stripe during batch operations.

**Workaround**: Add exponential backoff and retry logic.

**Affected Code**: `payment_processor.py`

**Status**: Implemented in v1.4.0

## Customer-Specific Issues

### Acme Corp Custom Field Bug

**Issue**: Custom field "department_code" validation rejects valid codes with dashes.

**Root Cause**: Regex pattern too strict: `^[A-Z0-9]+$` doesn't allow dashes.

**Fix**: Update regex to `^[A-Z0-9-]+$`

**Status**: Fixed in patch v1.3.1, awaiting customer confirmation

### TechCorp Data Migration Issue

**Issue**: Legacy data format has NULL values in required fields, causing import to fail.

**Workaround**: Pre-process data to replace NULL with appropriate defaults.

**Affected Migration**: `20240101_import_techcorp_legacy_data`

**Status**: Custom migration script created, in progress

## Documentation Gaps

- Missing runbook for handling database failover
- No guide for configuring custom authentication providers
- API deprecation timeline not clearly communicated
