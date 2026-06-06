#!/bin/bash
set -e

echo "🚀 Starting Agentic SDLC Stack..."
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Docker is running
echo "${BLUE}[1/4]${NC} Checking Docker daemon..."
if ! docker ps > /dev/null 2>&1; then
    echo "Starting Colima..."
    colima start
    sleep 30
fi
echo "${GREEN}✓${NC} Docker is running"
echo ""

# Start all services
echo "${BLUE}[2/4]${NC} Starting Docker services..."
docker-compose up -d
echo "${GREEN}✓${NC} Services started"
echo ""

# Wait for critical services to be healthy
echo "${BLUE}[3/4]${NC} Waiting for services to be ready (this takes 30-60 seconds)..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if docker exec postgres-dev pg_isready -U dev > /dev/null 2>&1 && \
       docker exec jenkins-asdlc curl -s http://localhost:8080/login > /dev/null 2>&1 && \
       docker exec gitea-dev curl -s http://localhost:3000 > /dev/null 2>&1; then
        break
    fi
    attempt=$((attempt + 1))
    sleep 2
done
echo "${GREEN}✓${NC} Services are ready"
echo ""

# Show status
echo "${BLUE}[4/4]${NC} Checking service status..."
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "(jenkins|gitea|asdlc|postgres|pgadmin|ollama|redis)" || true
echo ""

# Print access information
echo "═══════════════════════════════════════════════════════════════"
echo "${GREEN}✓ All services started successfully!${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "${YELLOW}📍 Access Your Services:${NC}"
echo ""
echo "  ${BLUE}Gitea${NC} (Git Server)"
echo "    URL: http://localhost:3000"
echo "    SSH: git@localhost:2222"
echo "    First time: Click 'Install Gitea', create admin user"
echo ""
echo "  ${BLUE}Jenkins${NC} (CI/CD)"
echo "    URL: http://localhost:8080"
echo "    First time: Complete initial setup, create admin user"
echo ""
echo "  ${BLUE}asdlc${NC} (Code Analysis)"
echo "    URL: http://localhost:8088"
echo "    API Key: (from .env file)"
echo ""
echo "  ${BLUE}PostgreSQL${NC} (Database)"
echo "    Host: localhost:5432"
echo "    User: dev"
echo "    Password: dev-password"
echo "    Databases: inventory, gitea"
echo ""
echo "  ${BLUE}pgAdmin${NC} (Database UI)"
echo "    URL: http://localhost:5050"
echo "    Email: admin@example.com"
echo "    Password: admin"
echo ""
echo "  ${BLUE}Ollama${NC} (LLM Engine)"
echo "    URL: http://localhost:11434"
echo "    (API only, no UI)"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "${YELLOW}📋 Next Steps:${NC}"
echo ""
echo "  1. Open http://localhost:3000 (Gitea setup)"
echo "  2. Open http://localhost:8080 (Jenkins setup)"
echo "  3. Read: docs/JENKINS_SETUP.md"
echo ""
echo "${YELLOW}⏹️  To Stop Everything:${NC}"
echo ""
echo "    docker-compose down"
echo "    colima stop"
echo ""
echo "═══════════════════════════════════════════════════════════════"
