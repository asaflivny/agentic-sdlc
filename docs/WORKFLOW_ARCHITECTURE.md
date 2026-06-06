# Workflow Architecture & Testing

Complete breakdown of each service and how they work together in your CI/CD + code analysis pipeline.

---

## 🔄 The Complete Workflow

```
Developer pushes code
        ↓
    Gitea
  (Git Server)
        ↓ (webhook on push)
   Jenkins
  (CI/CD Engine)
        ↓
   ┌───────────────────┐
   │  Run Tests        │
   │  Build App        │
   └────────┬──────────┘
            ↓
   ┌───────────────────────┐
   │  Call asdlc /scan     │
   │  (Code Analysis)      │
   └────────┬──────────────┘
            ↓
   ┌───────────────────────────┐
   │  asdlc + Ollama           │
   │  (LLM Agent Analysis)     │
   └────────┬──────────────────┘
            ↓
   ┌────────────────────────────────┐
   │  Store Findings in PostgreSQL  │
   │  Generate JUnit Reports        │
   └────────┬───────────────────────┘
            ↓
   ┌────────────────────────────────┐
   │  Jenkins Publishes Results     │
   │  - JUnit test report           │
   │  - Build description with      │
   │    findings summary            │
   └────────────────────────────────┘
```

---

## 📊 Each Service's Purpose

### **1. Gitea** (Port 3000)
**What it does:** Self-hosted Git server (like GitHub/Bitbucket but local)

**In your workflow:**
- Central repository for `inventory-tracker` code
- Triggers webhooks on `git push`
- Jenkins listens to these webhooks

**For testing:**
- Push test code to Gitea
- Verify that Jenkins webhooks trigger on push
- Test Git SSH access (port 2222)

**How to test:**
```bash
# Clone from Gitea
git clone http://localhost:3000/admin/inventory-tracker.git

# Push to Gitea
git push gitea main

# Verify webhook triggered Jenkins
# → Check Jenkins console logs
```

---

### **2. Jenkins** (Port 8080)
**What it does:** CI/CD automation server (orchestrates the entire pipeline)

**In your workflow:**
1. Receives webhook from Gitea (code pushed)
2. Pulls latest code
3. Runs tests/builds
4. Triggers asdlc analysis
5. Publishes reports

**For testing:**
- Verify jobs run on push
- Check that asdlc is called with correct parameters
- Verify findings are published as JUnit reports
- Monitor build logs for errors

**How to test:**
```bash
# 1. Go to Jenkins
open http://localhost:8080

# 2. Create a job that:
#    - Pulls from Gitea
#    - Runs tests
#    - Calls asdlc /scan
#    - Publishes JUnit reports

# 3. Push code to Gitea, watch Jenkins run

# 4. Check job console output
Jenkins → Job → Build #1 → Console Output
```

---

### **3. asdlc** (Port 8088)
**What it does:** Code analysis engine with AI agents

**In your workflow:**
- Receives `/scan` request from Jenkins
- Analyzes code diffs for:
  - Security issues (OWASP, secrets, crypto)
  - Performance problems (algorithms, I/O)
  - Code quality (bugs, logic errors)
  - Test coverage
  - Dependency vulnerabilities
- Returns findings as JUnit XML + JSON
- Posts findings back to Jenkins webhook

**For testing:**
- Verify asdlc can access the repo
- Test agent analysis locally
- Verify findings are correct
- Check webhook callback to Jenkins

**How to test:**
```bash
# Test asdlc directly
curl -X POST http://localhost:8088/scan \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_path": "/Users/asaf/Projects/inventory-tracker",
    "branch": "main",
    "jenkins_callback_url": "http://localhost:8080/webhook",
    "jenkins_job_name": "inventory-tracker-ci",
    "jenkins_build_number": 42,
    "jenkins_api_token": "your-token"
  }'

# Watch asdlc logs
docker logs asdlc-server -f

# Verify Jenkins received callback
docker logs jenkins-asdlc -f | grep "asdlc callback"
```

---

### **4. Ollama** (Port 11434)
**What it does:** Local LLM inference engine (runs AI models)

**In your workflow:**
- Provides the AI brain for asdlc agents
- Runs model: `qwen2.5-coder:14b` (or configured model)
- Analyzes code and generates findings
- No direct user interaction (API only)

**For testing:**
- Verify Ollama is healthy
- Check that asdlc can communicate with it
- Monitor inference time (slow = bottleneck)
- Test with different models if needed

**How to test:**
```bash
# Check Ollama is running
curl http://localhost:11434/api/tags

# View logs
docker logs ollama-dev -f

# List available models
docker exec ollama-dev ollama list

# Pull a different model if needed
docker exec ollama-dev ollama pull llama2
```

---

### **5. PostgreSQL** (Port 5432)
**What it does:** Persistent database storing all findings and metadata

**In your workflow:**
- Stores asdlc findings (what was found, severity, file, line)
- Stores Jenkins job history
- Stores Gitea repository data
- Stores pgAdmin configuration

**Databases:**
- `inventory` — inventory-tracker app data
- `gitea` — Gitea repositories
- `postgres` — Internal PostgreSQL data

**For testing:**
- Query findings after asdlc runs
- Verify data persistence (stop/start, data still there)
- Check database integrity
- Monitor query performance

**How to test:**
```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U dev -d inventory

# List tables
\dt

# View all findings (if asdlc stores them)
SELECT * FROM findings ORDER BY created_at DESC;

# Check Gitea data
docker-compose exec postgres psql -U dev -d gitea -c "\dt"
```

---

### **6. pgAdmin** (Port 5050)
**What it does:** Web UI for browsing/managing PostgreSQL databases

**In your workflow:**
- Visual database explorer
- Run SQL queries from browser
- Monitor database performance
- Optional (not critical to pipeline)

**For testing:**
- Browse findings after asdlc analysis
- Verify data was written correctly
- Run ad-hoc queries
- Export data for reporting

**How to test:**
```bash
# Access at http://localhost:5050
# Login: admin@example.com / admin

# Add server:
# Host: postgres-dev
# Port: 5432
# User: dev
# Password: dev-password

# Browse databases and tables
```

---

### **7. Redis** (Port 6379)
**What it does:** In-memory cache/queue (optional performance booster)

**In your workflow:**
- Currently NOT USED (optional)
- Could be used for:
  - Caching frequent queries
  - Queueing long-running jobs
  - Session storage

**For testing:**
- Not needed for basic workflow
- Keep running but can ignore for now

---

## 🧪 Testing the Complete Workflow

### **Full End-to-End Test (30 minutes)**

```bash
# 1. Start everything
./start-all.sh

# 2. Set up Gitea (first time only)
# - Open http://localhost:3000
# - Click "Install Gitea"
# - Create admin user

# 3. Push code to Gitea
cd /Users/asaf/Projects/inventory-tracker
git remote add gitea http://localhost:3000/admin/inventory-tracker.git
git push -u gitea main

# 4. Verify webhook triggered Jenkins
# - Open http://localhost:8080
# - See job "inventory-tracker-ci" running
# - Watch console output

# 5. Check asdlc analysis started
docker logs asdlc-server -f
# Look for: "=== [agent_name] SYSTEM PROMPT ==="

# 6. Verify findings in PostgreSQL
docker-compose exec postgres psql -U dev -d inventory -c \
  "SELECT COUNT(*) FROM findings;" 2>/dev/null || echo "Table doesn't exist yet"

# 7. Check Jenkins published results
# - Jenkins job → Build → Console Output
# - Should see JUnit reports published
# - Build description updated with findings summary

# 8. Browse in pgAdmin (optional)
open http://localhost:5050
# Login and explore findings
```

---

## 🎯 What Each App Needs From Others

```
┌─────────────────────────────────────────────────┐
│                    Jenkins                      │
│  Needs: Gitea (webhooks), asdlc (/scan API)    │
│  Provides: Job triggers to asdlc                │
└─────────────────────────────────────────────────┘
        ↑                          ↓
   Gitea              ┌──────────────────────────┐
Needs: PostgreSQL    │         asdlc            │
Provides: Webhooks   │ Needs: Ollama, PostgreSQL│
                     │ Provides: Findings       │
                     └──────────────────────────┘
                               ↓
                         ┌──────────────┐
                         │    Ollama    │
                         │ Needs: None  │
                         │ Provides: AI │
                         └──────────────┘
                               
   ┌────────────────────────────────────────┐
   │         PostgreSQL (backbone)          │
   │  Needs: None                           │
   │  Provides: Data storage for all apps   │
   └────────────────────────────────────────┘
```

---

## 📋 Quick Test Checklist

- [ ] Gitea starts and you can access http://localhost:3000
- [ ] Jenkins starts and you can access http://localhost:8080
- [ ] Push to Gitea triggers Jenkins webhook
- [ ] Jenkins job calls asdlc `/scan` endpoint
- [ ] asdlc analyzes code and finds issues
- [ ] Findings are stored in PostgreSQL
- [ ] Jenkins publishes JUnit reports
- [ ] pgAdmin can connect to PostgreSQL
- [ ] All data persists after `docker-compose down`
- [ ] Everything restarts with `./start-all.sh`

---

## 🔍 Debugging Tips

**Problem: Jenkins doesn't trigger on Gitea push**
```bash
# 1. Check Gitea webhook configuration
#    Gitea → repo → Settings → Webhooks
#    Should point to: http://jenkins-asdlc:8080/generic-webhook-trigger/invoke

# 2. Check Jenkins logs
docker logs jenkins-asdlc -f | grep webhook

# 3. Test webhook manually
curl -X POST http://localhost:8080/generic-webhook-trigger/invoke?token=gitea-push \
  -H "Content-Type: application/json" \
  -d '{"action": "push"}'
```

**Problem: asdlc doesn't analyze code**
```bash
# 1. Check asdlc can access the repo
docker exec asdlc-server ls -la /repos

# 2. Check asdlc logs
docker logs asdlc-server -f | grep ERROR

# 3. Test asdlc directly
curl -X POST http://localhost:8088/scan \
  -H "X-API-Key: your-api-key" \
  -d '{"repo_path": "/Users/asaf/Projects/inventory-tracker"}'
```

**Problem: PostgreSQL data lost**
```bash
# Check volumes are persistent
docker volume ls | grep agentic-sdlc

# Verify data after restart
docker-compose down
docker-compose up -d postgres
sleep 10
docker-compose exec postgres psql -U dev -d inventory -c "\dt"
```

---

## 🚀 Next Steps

1. Run through the "Full End-to-End Test" above
2. Push real code changes to Gitea
3. Watch Jenkins + asdlc analyze it automatically
4. Review findings in PostgreSQL / pgAdmin
5. Iterate on Jenkins pipeline (add more stages if needed)

---

## See Also

- [DOCKER_SETUP.md](DOCKER_SETUP.md) — How to manage containers
- [JENKINS_SETUP.md](JENKINS_SETUP.md) — Detailed Jenkins configuration
- [JENKINS_INTEGRATION.md](JENKINS_INTEGRATION.md) — asdlc + Jenkins API details
