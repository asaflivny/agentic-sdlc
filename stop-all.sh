#!/bin/bash
set -e

echo "🛑 Stopping Agentic SDLC Stack..."
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Stop docker services
echo "${BLUE}[1/2]${NC} Stopping Docker services..."
docker-compose down
echo "${GREEN}✓${NC} Docker services stopped"
echo ""

# Stop Colima
echo "${BLUE}[2/2]${NC} Stopping Colima (Docker daemon)..."
colima stop
echo "${GREEN}✓${NC} Colima stopped"
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo "${GREEN}✓ All services stopped${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "${YELLOW}💾 All data is persistent!${NC}"
echo "   Run './start-all.sh' next time to restart everything"
echo ""
