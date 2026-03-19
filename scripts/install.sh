#!/bin/bash
set -e

SKILL_DIR="$HOME/.claude/skills/agentcast"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$SKILL_DIR/scripts"

cp "$REPO_DIR/skill.md" "$SKILL_DIR/"
cp "$REPO_DIR/scripts/"*.sh "$SKILL_DIR/scripts/"
chmod +x "$SKILL_DIR/scripts/"*.sh

# Install necessary dependencies for raw protocol scripts
echo "Installing base dependencies..."
pip install cryptography httpx --quiet

echo ""
echo "✅ AgentCast skill installed to $SKILL_DIR"
echo "✅ Dependencies installed (cryptography, httpx)"
echo ""
echo "Next step: bash ~/.claude/skills/agentcast/scripts/register.sh"
