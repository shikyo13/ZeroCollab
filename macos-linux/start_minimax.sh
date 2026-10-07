#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + MiniMax API agent wrapper
# Usage: sh start_minimax.sh
# Requires MINIMAX_API_KEY environment variable.
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

# Check API key
if [ -z "$MINIMAX_API_KEY" ]; then
    echo "Error: MINIMAX_API_KEY environment variable is not set."
    echo "Get an API key at https://platform.minimax.io"
    echo "Then: export MINIMAX_API_KEY=your-key-here"
    exit 1
fi

exec sh "$(dirname "$0")/_launch.sh" wrapper_api.py minimax
