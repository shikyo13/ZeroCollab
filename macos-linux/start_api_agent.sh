#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + API agent wrapper
# Usage: sh start_api_agent.sh <agent_name>
# Example: sh start_api_agent.sh qwen
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

if [ -z "$1" ]; then
    echo "Usage: start_api_agent.sh <agent_name>"
    echo "Example: start_api_agent.sh qwen"
    exit 1
fi

exec sh "$(dirname "$0")/_launch.sh" wrapper_api.py "$1"
