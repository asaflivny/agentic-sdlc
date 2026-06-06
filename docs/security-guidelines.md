# Security Guidelines

## Input Validation

Always validate user input before processing. Never trust client-provided data.

- Use schema validation libraries (pydantic, zod, joi)
- Whitelist acceptable input patterns
- Reject invalid input with clear error messages
- Log suspicious input attempts

## Authentication & Authorization

- Use strong password hashing (bcrypt, argon2)
- Implement proper session management with secure tokens
- Enforce role-based access control (RBAC)
- Never store passwords in plain text
- Use HTTPS for all authentication endpoints

## SQL Injection Prevention

Always use parameterized queries. Never concatenate user input into SQL strings.

```python
# ❌ WRONG
query = f"SELECT * FROM users WHERE id = {user_id}"

# ✅ CORRECT
query = "SELECT * FROM users WHERE id = ?"
cursor.execute(query, (user_id,))
```

## Cross-Site Scripting (XSS)

- Escape user input before rendering in HTML
- Use templating engines that auto-escape by default
- Set Content-Security-Policy headers
- Use HttpOnly and Secure flags on cookies

## Cross-Site Request Forgery (CSRF)

- Implement CSRF tokens for state-changing operations
- Use SameSite cookie attribute
- Validate Origin and Referer headers
- Require explicit user confirmation for sensitive actions

## API Security

- Implement rate limiting to prevent abuse
- Use API keys or OAuth2 tokens for authentication
- Version your APIs and deprecate old versions
- Log all API access attempts
- Monitor for unusual patterns (DDoS, credential stuffing)

## Secret Management

- Never commit secrets to version control
- Use environment variables or secret management services
- Rotate secrets regularly
- Use different credentials per environment (dev, staging, prod)
- Encrypt secrets at rest

## Dependency Security

- Keep dependencies up to date
- Monitor for security vulnerabilities (CVE, OSV)
- Use tools like Snyk or Dependabot
- Review dependency licenses
- Audit transitive dependencies

## Error Handling

- Don't expose stack traces to users
- Log detailed errors server-side for debugging
- Return generic error messages to clients
- Never leak system information in error responses
