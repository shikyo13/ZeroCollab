#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + Gemini wrapper
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

# Warn if ripgrep is missing (Gemini CLI can hang on init - upstream bug)
if ! command -v rg >/dev/null 2>&1; then
    echo ""
    echo "  Warning: ripgrep (rg) not found on PATH."
    echo "  Gemini CLI can hang on \"Initializing...\" for several minutes."
    echo "  Fix: apt install ripgrep (Linux) or brew install ripgrep (macOS)"
    echo "  See: https://github.com/google-gemini/gemini-cli/issues/13986"
    echo ""
fi

export ZC_CLI=gemini
exec sh "$(dirname "$0")/_launch.sh" wrapper.py gemini
