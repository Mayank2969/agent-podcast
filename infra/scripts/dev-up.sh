#!/bin/bash
# Start AgentCast in development mode
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

if [ ! -f .env ]; then
    echo "No .env file found. Copying from .env.example..."
    cp .env.example .env
    echo "Please edit .env and add your ANTHROPIC_API_KEY before continuing."
    exit 1
fi

echo "Starting AgentCast (development)..."
docker compose -f infra/docker/docker-compose.yml up --build "$@"
