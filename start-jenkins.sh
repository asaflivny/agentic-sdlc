#!/bin/bash
set -e

echo "🚀 Starting Jenkins for asdlc integration..."
echo ""

# Check if Docker is running
if ! docker ps > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Start Jenkins
echo "📦 Starting Jenkins container..."
docker-compose -f jenkins-docker-compose.yml up -d

# Wait for Jenkins to be ready
echo "⏳ Waiting for Jenkins to start (30-60 seconds)..."
for i in {1..60}; do
    if docker exec jenkins-asdlc curl -s http://localhost:8080/login > /dev/null 2>&1; then
        echo "✓ Jenkins is ready!"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "⚠️  Jenkins startup timeout. Check logs: docker logs jenkins-asdlc"
        exit 1
    fi
    sleep 1
done

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "✓ Jenkins is running!"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "📍 Access Jenkins at: http://localhost:8080"
echo ""
echo "🔑 Initial Admin Password:"
echo "───────────────────────────────────────────────────────────"
JENKINS_PASS=$(docker exec jenkins-asdlc cat /var/jenkins_home/secrets/initialAdminPassword)
echo "$JENKINS_PASS"
echo "───────────────────────────────────────────────────────────"
echo ""
echo "📋 Next steps:"
echo "   1. Open http://localhost:8080 in your browser"
echo "   2. Paste the password above to unlock Jenkins"
echo "   3. Install suggested plugins (or just 'Install plugins')"
echo "   4. Create admin user (username: admin)"
echo "   5. Follow docs/JENKINS_SETUP.md to complete configuration"
echo ""
echo "⚙️  Commands:"
echo "   - View logs:    docker logs jenkins-asdlc -f"
echo "   - Stop:         docker-compose -f jenkins-docker-compose.yml down"
echo "   - Restart:      docker-compose -f jenkins-docker-compose.yml restart"
echo ""
