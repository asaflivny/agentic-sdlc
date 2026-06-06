# Docker Setup: Clone-on-Startup + Fetch-on-Webhook

**Architecture:**
```
Your local git server (docker volume)
    ↓
asdlc container (on startup: clones repos)
    ↓
/repos/inventory-tracker/ (inside container)
    ↓
Webhook arrives: auto-fetch from local git
    ↓
Analyze with agents
```

---

## docker-compose.yml (updated)

```yaml
version: '3.8'

services:
  asdlc:
    build: .
    ports:
      - "8088:8088"
    environment:
      - OLLAMA_BASE_URL=http://ollama:11434/v1
      - OLLAMA_MODEL=qwen2.5-coder:7b
      - WEBHOOK_SECRET=your-secret-here
      - LOG_LEVEL=INFO
      - REPOS_ROOT=/repos
      - GIT_CLONE_SOURCES=inventory-tracker:/git-source/inventory-tracker.git,other-repo:/git-source/other-repo.git
    volumes:
      - asdlc-data:/data
      - /path/to/local/git/repos:/git-source:ro   # ← Local git sources (read-only)
      - /repos:/repos                              # ← Where repos are cloned inside container
    depends_on:
      ollama:
        condition: service_healthy

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 10s
      timeout: 5s
      retries: 10

volumes:
  asdlc-data:
  ollama-data:
```

---

## How It Works

### On `docker-compose up`:

1. **Container starts**
2. **asdlc reads config:**
   ```
   REPOS_ROOT=/repos
   GIT_CLONE_SOURCES=inventory-tracker:/git-source/inventory-tracker.git
   ```
3. **On startup, clones from local source:**
   ```
   git clone /git-source/inventory-tracker.git /repos/inventory-tracker
   ```
4. **Logs show:**
   ```
   repos_init action=clone repo=inventory-tracker source=/git-source/inventory-tracker.git dest=/repos/inventory-tracker
   repo_init clone_complete repo=inventory-tracker
   repos_init complete count=1
   ```

### When webhook arrives:

1. **Receive push event** (repo=inventory-tracker, branch=main)
2. **Auto-fetch from cloned repo:**
   ```
   git fetch --all --tags              # ← Pulls latest from local git
   git checkout main
   ```
3. **Analyze code** (agents run on `/repos/inventory-tracker`)

---

## Setup Steps

### 1. Prepare local git sources

You have two options:

#### **Option A: Bare repo (recommended for server)**
```bash
# On your git server or dev machine
cd /path/to/local/git
git clone --bare https://bitbucket.org/yourorg/inventory-tracker.git inventory-tracker.git
# Creates: /path/to/local/git/inventory-tracker.git/
```

#### **Option B: Regular repo**
```bash
# On your git server or dev machine
cd /path/to/local/git
git clone https://bitbucket.org/yourorg/inventory-tracker.git
# Creates: /path/to/local/git/inventory-tracker/
```

### 2. Update docker-compose.yml

```yaml
volumes:
  # Mount local git repos as read-only
  - /path/to/local/git:/git-source:ro
```

Replace `/path/to/local/git` with your actual path.

### 3. Set GIT_CLONE_SOURCES env var

```yaml
environment:
  - GIT_CLONE_SOURCES=inventory-tracker:/git-source/inventory-tracker.git
```

For multiple repos:
```yaml
- GIT_CLONE_SOURCES=inventory-tracker:/git-source/inventory-tracker.git,other-repo:/git-source/other-repo.git,my-app:/git-source/my-app.git
```

### 4. Start containers

```bash
docker-compose up -d

# Watch logs
docker-compose logs -f asdlc

# Should see:
# repos_init action=clone repo=inventory-tracker ...
# repo_init clone_complete repo=inventory-tracker
# asdlc ready model=qwen2.5-coder:7b
```

### 5. Test webhook

```bash
# Inside container, push to inventory-tracker
# OR from outside: trigger webhook manually

curl -X POST http://localhost:8088/git/push \
  -H "Content-Type: application/json" \
  -d '{
    "repository": {"name": "inventory-tracker", "clone_url": "/repos/inventory-tracker"},
    "branch": "main",
    "after": "abc123...",
    "before": "def456...",
    "commits": [{"id": "abc123", "message": "test", "author": {"name": "User"}, "modified": ["file.py"]}]
  }'
```

Check logs:
```
repo_sync complete repo=inventory-tracker branch=main
workflow=quick_review (or appropriate workflow)
agent=code_reviewer turn=1 tool_calls=none
...
completed run_id=xxx workflow=quick_review repo=inventory-tracker findings=N
```

---

## Update Flow (Key Point)

Your local git server should be **regularly updated** with remote changes:

```bash
# On your git server (cron job or CI)
#!/bin/bash
cd /path/to/local/git/inventory-tracker.git
git fetch --all --tags

# Or for regular repo:
cd /path/to/local/git/inventory-tracker
git fetch --all --tags
git pull origin main
```

Then when asdlc webhook arrives, it fetches from the local repo (which is already up-to-date).

---

## Network Flow

```
                    Your Network
    ┌──────────────────────────────────┐
    │  Git Server (docker volume)      │
    │  /git-source/inventory-tracker/  │
    │  (stays in sync with Bitbucket)  │
    └────────────┬──────────────────────┘
                 │
                 │ volume mount (read-only)
                 ↓
    ┌──────────────────────────────────┐
    │  asdlc container                 │
    │  /git-source ← /git-source       │
    │  /repos/inventory-tracker ← cloned here on startup
    │                                  │
    │  On webhook:                     │
    │  git fetch (from /git-source)    │
    │  analyze                         │
    └──────────────────────────────────┘
```

---

## Troubleshooting

### Clone failed on startup

```
repos_init clone_failed repo=inventory-tracker source_not_found path=/git-source/inventory-tracker.git
```

**Fix:**
- Check volume mount: `docker-compose ps -v`
- Verify source path exists on host: `ls /path/to/local/git/inventory-tracker.git`
- Check read-only flag: `:ro` in docker-compose.yml

### Webhook returns "repo not found"

```
repo_sync skipped ... reason=not_found path=/repos/inventory-tracker
```

**Fix:**
- Check container /repos: `docker exec asdlc ls -la /repos`
- Verify GIT_CLONE_SOURCES env var: `docker exec asdlc env | grep GIT_CLONE`
- Check logs for clone errors on startup: `docker-compose logs asdlc | grep clone`

### Fetch fails in webhook

```
repo_sync failed ... error=fatal: not a git repository
```

**Fix:**
- Verify source is a valid repo: `cd /path/to/local/git/inventory-tracker.git && git rev-parse --git-dir`
- Re-clone if corrupted: `rm -rf /git-source/inventory-tracker.git && git clone --bare ...`

---

## Performance Notes

- **Clone on startup:** ~1-5 seconds per repo (one-time)
- **Fetch on webhook:** ~500ms per repo (fast)
- **Analysis:** 10-30 seconds (depends on diff size and model)

For multiple repos:
```
GIT_CLONE_SOURCES=\
  inventory-tracker:/git-source/inventory-tracker.git,\
  service-a:/git-source/service-a.git,\
  service-b:/git-source/service-b.git
```

All cloned in parallel on startup.

---

## Production Checklist

- ✅ Git server volume mounted read-only (`:ro`)
- ✅ Local git repos updated regularly (cron or CI)
- ✅ WEBHOOK_SECRET configured
- ✅ REPOS_ROOT writable (`/repos`)
- ✅ Slack/email notifications set up
- ✅ Monitor logs for clone/fetch errors
- ✅ Backup `asdlc-data` volume (findings database)

---

## Next Steps

1. Prepare local git sources (bare or regular repo)
2. Update `docker-compose.yml` with volume mounts
3. Set `GIT_CLONE_SOURCES` environment variable
4. Run `docker-compose up`
5. Test with a webhook
6. Set up cron to keep local git repos updated
