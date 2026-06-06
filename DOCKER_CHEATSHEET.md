# Docker/Colima Quick Cheatsheet

Save this file. Copy/paste these commands every day.

---

## 🚀 Morning: Start Everything

```bash
# 1. Start Colima (Docker daemon)
colima start

# 2. Wait for it
sleep 10

# 3. Start all services (Jenkins, PostgreSQL, asdlc, Ollama, etc.)
docker-compose up -d

# 4. Verify everything is running
docker ps
```

**Expected output:** 6+ containers running (jenkins, asdlc, ollama, postgres, pgadmin, redis)

---

## 📍 Access Your Services

| Service | URL | Login |
|---|---|---|
| **Jenkins** | http://localhost:8080 | Initial password from setup |
| **pgAdmin** | http://localhost:5050 | admin@example.com / admin |
| **PostgreSQL** | localhost:5432 | user: dev / password: dev-password |
| **asdlc** | http://localhost:8088 | API key in `.env` |
| **Ollama** | http://localhost:11434 | (API only, no UI) |
| **Redis** | localhost:6379 | (CLI via docker) |

---

## 💻 Common Commands

### Check Status

```bash
# What's running?
docker ps

# What's running + stopped?
docker ps -a

# How much resource are we using?
colima status
```

### View Logs

```bash
# All service logs
docker-compose logs

# Follow Jenkins logs in real-time
docker-compose logs -f jenkins

# Follow asdlc logs
docker-compose logs -f asdlc

# PostgreSQL logs
docker-compose logs postgres

# Stop following: Press Ctrl+C
```

### Restart a Service

```bash
# Restart Jenkins
docker-compose restart jenkins

# Restart asdlc
docker-compose restart asdlc

# Restart PostgreSQL
docker-compose restart postgres
```

### Execute Commands in Container

```bash
# Get shell in postgres container
docker-compose exec postgres bash

# Run psql (PostgreSQL CLI)
docker-compose exec postgres psql -U dev -d inventory

# Run shell in Jenkins
docker-compose exec jenkins bash
```

---

## 🛑 Evening: Stop Everything

```bash
# 1. Stop all services (keep volumes/databases)
docker-compose down

# 2. Stop Colima
colima stop
```

**Done!** Everything is cleaned up. Databases are preserved for next time.

---

## 🔄 Reset Everything (Last Resort)

```bash
# ⚠️  WARNING: Deletes all databases!

# Stop and delete everything
docker-compose down -v

# Stop Colima
colima stop

# Start fresh
colima start
sleep 10
docker-compose up -d
```

---

## 🆘 Troubleshooting

### Container won't start

```bash
# Check logs
docker-compose logs <service-name>

# Restart
docker-compose restart <service-name>
```

### "Port already in use"

```bash
# Which process is using port 8080?
lsof -i :8080

# Kill it (if safe)
kill -9 <PID>

# Or just stop Colima
docker-compose down
colima stop
```

### "Docker daemon is not running"

```bash
colima start
sleep 10
docker ps
```

### Out of disk space

```bash
# Clean up unused Docker data
docker system prune -a

# If still stuck
colima delete
colima start
docker-compose up -d
```

---

## 📊 One-Liners for Quick Tasks

```bash
# Count running containers
docker ps | wc -l

# Remove all stopped containers
docker container prune -f

# View PostgreSQL data
docker-compose exec postgres psql -U dev -c "SELECT * FROM table_name;"

# Get a container's IP
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' <container-id>

# Export PostgreSQL dump
docker-compose exec postgres pg_dump -U dev inventory > backup.sql

# Import PostgreSQL dump
docker-compose exec -T postgres psql -U dev inventory < backup.sql
```

---

## 🎯 Typical Workflow

### 9 AM: Start Work
```bash
colima start && sleep 10 && docker-compose up -d
# Go to http://localhost:8080 (Jenkins)
# Go to http://localhost:5050 (PostgreSQL)
```

### During Day
```bash
# Check logs while building
docker-compose logs -f asdlc

# If something breaks
docker-compose restart <service>

# Need database access
docker-compose exec postgres psql -U dev -d inventory
```

### 5 PM: Leave Work
```bash
docker-compose down
colima stop
```

---

## 📝 Save This

**Copy this to a file in your Documents:**
```bash
cp DOCKER_CHEATSHEET.md ~/Documents/docker-commands.md
```

Then whenever you need it:
```bash
cat ~/Documents/docker-commands.md
# or open in editor
code ~/Documents/docker-commands.md
```

---

## 🔗 Full Documentation

For detailed info:
- [DOCKER_SETUP.md](docs/DOCKER_SETUP.md) — Complete guide
- [JENKINS_SETUP.md](docs/JENKINS_SETUP.md) — Jenkins configuration
