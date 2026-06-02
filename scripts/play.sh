#!/usr/bin/env bash
# RPG World Engine — One-click launch script
# Usage: ./scripts/play.sh [content_pack] [--fake | --ollama | --minimax | --openai]

set -euo pipefail

# Script lives in scripts/; the project root is its parent.
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LLM_BACKEND="ollama"
CONTENT_PACK="fixtures/content_packs/valid_frontier_town.json"

# Load local secrets (.env) so cloud backends pick up their API keys.
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; . "$PROJECT_ROOT/.env"; set +a
fi

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --fake)    LLM_BACKEND="fake" ;;
        --ollama)  LLM_BACKEND="ollama" ;;
        --minimax) LLM_BACKEND="minimax" ;;
        --openai)  LLM_BACKEND="openai" ;;
        *)         CONTENT_PACK="$arg" ;;
    esac
done

echo "══════════════════════════════════════════════════"
echo "  RPG World Engine — LLM-driven text RPG"
echo "══════════════════════════════════════════════════"
echo ""

# Check Python environment
if [ ! -d "$PROJECT_ROOT/.venv" ]; then
    echo "❌ Virtual environment not found at $PROJECT_ROOT/.venv"
    echo "   Please create it: python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
    exit 1
fi

PYTHON="$PROJECT_ROOT/.venv/bin/python"

# Check content pack exists
if [ ! -f "$PROJECT_ROOT/$CONTENT_PACK" ]; then
    echo "❌ Content pack not found: $CONTENT_PACK"
    echo "   Available packs:"
    ls -1 "$PROJECT_ROOT/fixtures/content_packs/" 2>/dev/null || true
    exit 1
fi

# Check Ollama if using Ollama backend
if [ "$LLM_BACKEND" = "ollama" ]; then
    echo "🔍 Checking Ollama..."
    if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "⚠️  Ollama is not running on localhost:11434"
        echo "   Please start it: ollama serve"
        echo ""
        echo "   Or use FakeLLMProvider (deterministic, no LLM):"
        echo "   ./play.sh --fake"
        exit 1
    fi

    # Check if gpt-oss:latest is available
    if ! curl -s http://localhost:11434/api/tags | grep -q "gpt-oss:latest"; then
        echo "⚠️  Model 'gpt-oss:latest' not found in Ollama"
        echo "   Pull it: ollama pull gpt-oss:latest"
        exit 1
    fi
    echo "✅ Ollama ready (gpt-oss:latest)"
    echo ""
fi

# Check API key for cloud (OpenAI-compatible) backends.
if [ "$LLM_BACKEND" = "minimax" ] && [ -z "${MINIMAX_API_KEY:-}" ]; then
    echo "❌ MINIMAX_API_KEY not set. Add it to $PROJECT_ROOT/.env"
    exit 1
fi
if [ "$LLM_BACKEND" = "openai" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "❌ OPENAI_API_KEY not set. Add it to $PROJECT_ROOT/.env"
    exit 1
fi

# Launch
echo "🎮 Starting game..."
echo "   Content pack: $CONTENT_PACK"
echo "   LLM backend:  $LLM_BACKEND"
echo "   Save dir:     saves/"
echo ""
echo "Commands: /help for help, /quit to exit"
echo ""

export PYTHONPATH="$PROJECT_ROOT/src"
exec "$PYTHON" -m verisaria run "$CONTENT_PACK" --llm "$LLM_BACKEND" --save-dir saves
