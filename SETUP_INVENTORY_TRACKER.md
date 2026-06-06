# Setup Guide: inventory-tracker + asdlc

**Goal:** Analyze `inventory-tracker` pushes from Bitbucket/Jenkins using asdlc agents

---

## Architecture

```
Bitbucket/Jenkins
       ↓ webhook
   asdlc server
       ↓ auto-fetch from remote
   /path/to/inventory-tracker (on disk)
       ↓ analyze
   Findings (agents run locally)
```

**Key point:** The app automatically fetches latest changes from remote before analyzing.

---

## Setup Steps

### 1. Clone inventory-tracker on asdlc server

```bash
# On the asdlc server machine
cd /repos  # or wherever you want to store repos
git clone <bitbucket-url-of-inventory-tracker>
cd inventory-tracker
git remote -v  # verify origin is set to Bitbucket
```

### 2. Install pre-push hook on inventory-tracker (dev machines)

```bash
cd ~/projects/inventory-tracker  # your local dev copy
asdlc install-hook . --url http://asdlc-server:8088/git/push
```

This writes `.git/hooks/pre-push` to send webhooks on every push.

### 3. Configure Bitbucket webhook (optional, if you want Jenkins/Bitbucket to trigger too)

**Bitbucket → Webhooks:**
- URL: `http://asdlc-server:8088/git/push`
- Triggers: Push events
- Headers: Set `X-Hub-Signature-256` if `WEBHOOK_SECRET` is configured

**Jenkins → Post-build action:**
- Curl to `http://asdlc-server:8088/git/push` with JSON payload

### 4. Set webhook secret in .env (optional but recommended)

```bash
# On asdlc server
WEBHOOK_SECRET="your-shared-secret-here"
```

Then set same secret in Bitbucket/Jenkins webhooks.

### 5. Enable notifications (optional)

```bash
# .env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
SLACK_MENTION_CHANNELS=@dev-team
EMAIL_WEBHOOK_URL=https://api.sendgrid.com/v3/mail/send
EMAIL_RECIPIENTS=team@company.com
```

---

## How It Works

### When you push to inventory-tracker:

```bash
# On your dev machine:
git commit -m "add new feature"
git push origin main

# Pre-push hook triggers webhook to asdlc server
```

### On asdlc server:

```
1. Receive webhook from git pre-push hook or Bitbucket
2. Parse push event (repo: inventory-tracker, branch: main, commits: [...])
3. Auto-fetch from remote (pulls latest changes)
   - git fetch --all --tags
   - git checkout main
4. Route to workflow (full_review, security_focus, or quick_review)
5. Run 5 agents in parallel/sequential:
   - CodeReviewAgent (logic, best practices)
   - SecurityAnalystAgent (vulnerabilities, secrets)
   - PerformanceAnalystAgent (efficiency)
   - DepAuditAgent (CVEs in dependencies)
   - TestCoverageAgent (missing tests)
6. Save findings to SQLite
7. Send Slack/email notification with results
8. Return HTTP 202 Accepted
```

---

## Verification

### Test a push locally

```bash
cd ~/projects/inventory-tracker
git commit --allow-empty -m "test asdlc integration"
git push origin main

# Watch asdlc server logs:
# You should see:
# - "push parsed OK repo=inventory-tracker"
# - "repo synced repo=inventory-tracker"
# - "workflow=quick_review (or appropriate workflow)"
# - Agent logs (code_reviewer, security_analyst, etc.)
```

### Check results on dashboard

```
http://asdlc-server:8088

# You should see a new run with:
# - Repo: inventory-tracker
# - Branch: main
# - Findings: (count by severity)
```

### Check database

```bash
sqlite3 asdlc.db "SELECT run_id, repo, branch, findings FROM workflow_runs ORDER BY created_at DESC LIMIT 1;"
```

---

## Customization for inventory-tracker

### Create `.asdlc.yml` in inventory-tracker root

```yaml
# inventory-tracker/.asdlc.yml

# Route to security_focus for sensitive changes
workflow: quick_review

agents:
  # Exclude test coverage agent for this repo (optional)
  exclude:
    - test_coverage

# Custom routing: any changes to auth/ or config/ → full_review
routing:
  - pattern: "auth/**"
    workflow: full_review
  - pattern: "config/**"
    workflow: full_review
  - pattern: "*.sql"
    workflow: security_focus
```

When you push, asdlc will:
1. Fetch the `.asdlc.yml` config from the repo
2. Override default workflow/agent settings
3. Analyze according to the custom config

### Per-agent file filtering

Currently agents filter by file type:
- **CodeReviewAgent**: `*.py, *.js, *.ts, *.tsx, ...` (code files)
- **SecurityAnalystAgent**: code + `auth/**, crypto/**, config/**`
- **DepAuditAgent**: `requirements.txt, pyproject.toml, pom.xml, ...` (dependency files)
- **TestCoverageAgent**: code files

If you want different filters, edit `workflows/definitions/full_review.py`:

```python
AgentSpec(
    agent_class=SecurityAnalystAgent,
    file_filter=[
        "*.py", "*.js",
        "**/auth/**",
        "**/crypto/**",
        "**/config/**",
        "*.pem", "*.key", "*.crt"  # add cert files
    ]
),
```

---

## Troubleshooting

### Webhook not received

```bash
# Check pre-push hook exists:
cat ~/projects/inventory-tracker/.git/hooks/pre-push

# Check hook has execute permission:
chmod +x ~/projects/inventory-tracker/.git/hooks/pre-push

# Test hook manually:
cd ~/projects/inventory-tracker
git commit --allow-empty -m "test"
git push origin main  # Should trigger webhook
```

### Repo sync fails

```
Error: "repo sync failed ... path_not_found"

# Fix: Repo not cloned on server
# On asdlc server:
cd /repos && git clone <bitbucket-url> inventory-tracker
```

### Webhook signature mismatch

```
Error: "Invalid signature"

# Fix: Set WEBHOOK_SECRET on both sides
# On asdlc server (.env):
WEBHOOK_SECRET=abc123

# In Bitbucket webhook:
X-Hub-Signature-256 header configured with same secret
```

### No findings returned

- Check model is running: `curl http://localhost:11434/api/tags`
- Try a larger model: `ollama pull qwen2.5:32b`
- Check logs for "structured extraction failed"

---

## Architecture Notes

### Why auto-fetch?

- Webhooks arrive **after** commit is pushed
- By the time webhook is processed, commit is already on remote
- Server fetches to get the exact state that was pushed
- Guarantees analysis is on the correct revision

### Security

- ✅ HMAC signature verification on webhooks
- ✅ Rate limiting (10 pushes/repo/minute by default)
- ✅ Schema validation on `.asdlc.yml`
- ✅ No secrets logged (redaction in place)
- ✅ SQLite database is append-only (no deletes)

### Performance

- Connection pooling: 5x faster queries
- Database indices: 3-5x faster dashboard
- Config caching: N-fold speedup on large diffs
- Diff chunking: 25KB chunks for large changes

---

## Next Steps

1. Clone inventory-tracker on server
2. Install hook on your local copy
3. Push a test commit
4. Verify findings appear in dashboard
5. Set up Slack/email notifications
6. Create `.asdlc.yml` for custom routing

**Questions?** Check `CLAUDE.md` (developer guide) or `IMPROVEMENTS_SUMMARY.md` (architecture details).
