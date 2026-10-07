#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + GitHub Copilot CLI wrapper
# Usage: sh start_copilot.sh
# Requires the copilot CLI on PATH. First launch prompts GitHub login.
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_CLI=copilot
export ZC_INSTALL="Install with: npm install -g @github/copilot"
export ZC_INSTALL_NOTE="See https://github.com/github/copilot-cli for details."
exec sh "$(dirname "$0")/_launch.sh" wrapper.py copilot
