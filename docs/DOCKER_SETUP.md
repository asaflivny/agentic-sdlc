# Docker & Colima Setup Guide

Complete guide to managing Docker services on macOS using Colima (lightweight Docker runtime).

## What is Colima?

**Colima** = Container Lightweight Machine for macOS

- Lightweight Docker runtime (uses Lima VM instead of Docker Desktop)
- Runs on macOS (Intel and Apple Silicon)
- Lower resource usage than Docker Desktop
- Command-line only (no fancy UI, but that's fine for development)
- Free and open source

---

## Installation

### Check if Colima is Already Installed

```bash
colima version
```

If you see a version number, you're done with installation ✅

### Install Colima (if needed)

```bash
# Using Homebrew (recommended)
brew install colima docker docker-compose

# Verify installation
colima version
docker --version
docker-compose --version
```

---

## Starting Docker

### Start Colima (One-time, then stays running in background)

```bash
colima start
```

Output will be:
```
INFO Starting ...
INFO Mounting /Users/asaf as /Users/asaf
INFO Starting...
✓ Done
```

**⏳ First startup takes 30-60 seconds. Subsequent startups are faster.**

### Verify Docker is Running

```bash
docker ps
```

You should see:
```
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
```

(Empty initially, but the table headers show Docker is working ✅)

### Check Docker/Colima Status Anytime

```bash
# Is Docker running?
docker ps

# Detailed Colima status
colima status
```

Output when running:
```
RUNNING   Colima
RUNNING   Docker Engine
RUNNING   Docker Buildx
```

---

## Stopping Docker

When you're done developing:

```bash
colima stop
```

Takes ~10 seconds. Docker will be completely shut down.

**Note:** Don't just close the terminal. Properly stop Colima to free up resources.

---

## Managing Colima

### Common Commands

```bash
# Start the Docker daemon
colima start

# Stop the Docker daemon
colima stop

# Restart (useful if something breaks)
colima restart

# View status
colima status

# View logs (for debugging)
colima logs

# Remove Colima VM completely (resets everything)
colima delete
```

### Check Resource Usage

```bash
# How much CPU/memory is Colima using?
top  # Press 'q' to exit
# or
ps aux | grep -i colima
```

---

## Docker Compose: Run Multiple Services

Use `docker-compose` to start multiple services (Jenkins, PostgreSQL, etc.) with one command.

### Create a `docker-compose.yml` file

Example for Jenkins + PostgreSQL + asdlc:

```yaml
version: '3.8'

services:
  # Jenkins CI/CD Server
  jenkins:
    image: jenkins/jenkins:lts
    container_name: jenkins-asdlc
    ports:
      - "8080:8080"      # Web UI
      - "50000:50000"    # Agent port
    volumes:
      - jenkins_home:/var/jenkins_home
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - JENKINS_OPTS=--install-plugins=pipeline:latest,generic-webhook-trigger:latest
    networks:
      - dev-network

  # PostgreSQL Database
  postgres:
    image: postgres:16
    container_name: postgres-dev
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: dev
      POSTGRES_PASSWORD: dev-password
      POSTGRES_DB: inventory
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - dev-network

  # Optional: pgAdmin (PostgreSQL UI)
  pgadmin:
    image: dpage/pgadmin4:latest
    container_name: pgadmin-dev
    ports:
      - "5050:80"
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@example.com
      PGADMIN_DEFAULT_PASSWORD: admin
    networks:
      - dev-network

  # asdlc (optional, if running in Docker)
  # asdlc:
  #   build: .
  #   container_name: asdlc
  #   ports:
  #     - "8088:8088"
  #   networks:
  #     - dev-network

volumes:
  jenkins_home:
  postgres_data:

networks:
  dev-network:
    driver: bridge
```

### Start All Services

```bash
# First, start Colima
colima start

# Then, start all services from docker-compose.yml
docker-compose up -d

# Verify all services are running
docker ps
```

You should see:
```
CONTAINER ID   IMAGE                    STATUS              PORTS
abc123...      jenkins/jenkins:lts      Up 2 minutes        0.0.0.0:8080->8080/tcp
def456...      postgres:16              Up 2 minutes        0.0.0.0:5432->5432/tcp
ghi789...      dpage/pgadmin4:latest    Up 2 minutes        0.0.0.0:5050->80/tcp
```

### Access Services

| Service | URL | Credentials |
|---|---|---|
| **Jenkins** | http://localhost:8080 | See initial password |
| **PostgreSQL** | localhost:5432 | user: `dev`, password: `dev-password` |
| **pgAdmin** | http://localhost:5050 | user: `admin@example.com`, password: `admin` |

### Stop All Services

```bash
# Stop services but keep volumes
docker-compose down

# Stop services AND delete volumes (reset databases)
docker-compose down -v

# Then stop Colima
colima stop
```

---

## Daily Workflow

### Morning (Start Everything)

```bash
# 1. Start Docker
colima start

# 2. Wait for it to be ready
sleep 10

# 3. Start all services
docker-compose up -d

# 4. Verify
docker ps
```

### Work

```bash
# View logs
docker-compose logs -f jenkins    # Follow Jenkins logs
docker-compose logs postgres      # PostgreSQL logs

# Access services at:
# - Jenkins: http://localhost:8080
# - PostgreSQL: localhost:5432
# - pgAdmin: http://localhost:5050
```

### Evening (Stop Everything)

```bash
# 1. Stop services
docker-compose down

# 2. Stop Colima
colima stop
```

---

## Quick Reference: Essential Docker Commands

### Containers

```bash
# List running containers
docker ps

# List all containers (including stopped)
docker ps -a

# Start a stopped container
docker start <container-name>

# Stop a running container
docker stop <container-name>

# Restart a container
docker restart <container-name>

# View container logs
docker logs <container-name>

# Follow logs in real-time
docker logs -f <container-name>

# Execute command in container
docker exec -it <container-name> bash

# Remove a container
docker rm <container-name>
```

### Docker Compose

```bash
# Start all services in background
docker-compose up -d

# Start with logs visible
docker-compose up

# Stop services
docker-compose down

# View logs
docker-compose logs

# Follow specific service logs
docker-compose logs -f <service-name>

# Restart a service
docker-compose restart <service-name>

# View running services
docker-compose ps

# Execute command in a service
docker-compose exec <service-name> bash
```

### Images

```bash
# List images
docker images

# Pull an image
docker pull <image-name>

# Remove an image
docker rmi <image-name>

# Build from Dockerfile
docker build -t <image-name> .
```

### Cleanup

```bash
# Remove unused containers
docker container prune

# Remove unused images
docker image prune

# Remove everything (be careful!)
docker system prune -a
```

---

## Troubleshooting

### "Cannot connect to Docker daemon"

**Problem:** `docker ps` returns error

**Solution:**
```bash
colima start
# Wait 30 seconds
docker ps
```

### "Port already in use"

**Problem:** `docker-compose up` fails with "port 8080 already in use"

**Solution:**
```bash
# Find what's using the port
lsof -i :8080

# Or just change the port in docker-compose.yml
# From: "8080:8080"
# To:   "8081:8080"  (use 8081 on your machine)
```

### "Out of disk space"

**Problem:** Docker complains about no space

**Solution:**
```bash
# Clean up unused Docker data
docker system prune -a

# If still problematic, reset Colima
colima delete
colima start
```

### "Permissions denied"

**Problem:** Docker commands fail with permission error

**Solution:**
```bash
# Add your user to docker group
sudo usermod -aG docker $USER

# Log out and back in, or:
newgrp docker
```

### Colima won't start

**Problem:** `colima start` hangs or fails

**Solution:**
```bash
# Check status
colima status

# Try restarting macOS, then:
colima start

# If still broken, reset:
colima delete
colima start
```

---

## Docker Compose File Locations

Store `docker-compose.yml` files in:

```
~/Projects/
├── agentic-sdlc/
│   └── jenkins-docker-compose.yml    (for just Jenkins)
│   └── docker-compose.yml             (for all services)
├── inventory-tracker/
│   └── docker-compose.yml             (if needed)
```

### Using Multiple Compose Files

```bash
# Start Jenkins
docker-compose -f agentic-sdlc/jenkins-docker-compose.yml up -d

# Start all dev services
docker-compose -f docker-compose.yml up -d

# Check all running
docker ps
```

---

## Resource Limits (Optional)

By default, Colima gets 2 CPUs and 4GB RAM. Increase if needed:

```bash
# Check current settings
colima status

# Stop Colima
colima stop

# Edit config (opens in vim)
colima start --cpu 4 --memory 8

# Or permanently set in ~/.colima/profile
cat ~/.colima/default/colima.yaml
```

---

## Useful Links

- [Colima GitHub](https://github.com/abiosoft/colima)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Docker Hub](https://hub.docker.com) (find images)

---

## See Also

- [JENKINS_SETUP.md](JENKINS_SETUP.md) — Jenkins configuration
- [JENKINS_INTEGRATION.md](JENKINS_INTEGRATION.md) — asdlc + Jenkins integration
