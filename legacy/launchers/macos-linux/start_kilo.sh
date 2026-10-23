#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + Kilo wrapper
# Usage: sh start_kilo.sh [provider/model]
#   e.g. sh start_kilo.sh anthropic/claude-sonnet-4-20250514
#   Omit the model to use Kilo's configured default.
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_CLI=kilo
if [ -n "$1" ]; then
    exec sh "$(dirname "$0")/_launch.sh" wrapper.py kilo -- -m "$1"
fi
exec sh "$(dirname "$0")/_launch.sh" wrapper.py kilo
