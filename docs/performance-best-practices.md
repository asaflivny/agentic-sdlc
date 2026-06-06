# Performance Best Practices

## Database Optimization

### Indexing

- Add indexes to frequently queried columns
- Use composite indexes for queries with multiple WHERE conditions
- Monitor query performance with EXPLAIN plans
- Avoid over-indexing (slows down writes)

### Query Optimization

- Use SELECT specific columns, not SELECT *
- Avoid N+1 query problems (load related data in one query)
- Use pagination for large result sets
- Cache query results when appropriate

### Connection Management

- Use connection pooling
- Set reasonable connection timeouts
- Close connections properly (use context managers)
- Monitor connection pool usage

## Caching Strategies

### In-Memory Caching

- Use for frequently accessed, rarely changed data
- Set appropriate TTL (time-to-live)
- Implement cache invalidation on data changes
- Monitor cache hit rates

### HTTP Caching

- Set Cache-Control headers appropriately
- Use ETags for conditional requests
- Implement Last-Modified headers
- Use CDNs for static content

## API Performance

### Response Size

- Compress responses (gzip)
- Return only necessary fields
- Implement field selection (GraphQL or sparse fieldsets)
- Paginate large response bodies

### Rate Limiting

- Implement rate limiting to protect server
- Use sliding window algorithms (better than fixed window)
- Return 429 status with Retry-After header
- Log rate limit violations

## Async/Concurrency

- Use async operations for I/O-bound tasks (database, HTTP)
- Avoid blocking operations in request handlers
- Use thread pools for CPU-bound work
- Monitor for deadlocks and race conditions

## Memory Management

- Monitor memory usage in production
- Avoid memory leaks (unreleased connections, circular references)
- Use generators for large data sets
- Profile memory-intensive operations

## Code Performance

### Algorithms

- Choose appropriate algorithms for the task
- Avoid O(n²) algorithms when O(n log n) exists
- Profile hot paths before optimizing
- Use memoization for expensive calculations

### Profiling

- Use profilers to find bottlenecks
- Profile both CPU and memory usage
- Test with realistic data volumes
- Monitor production performance metrics

## Scaling Patterns

### Vertical Scaling

- Add more CPU/memory to servers
- Increase database resources
- Useful when single application instance is bottleneck

### Horizontal Scaling

- Add multiple application instances
- Use load balancing
- Ensure stateless application design
- Use distributed caching (Redis)

## Monitoring & Observability

- Track response times (p50, p95, p99)
- Monitor error rates and types
- Set up alerts for performance degradation
- Collect logs and metrics for analysis
