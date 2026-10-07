#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + CodeBuddy wrapper
# Usage: sh start_codebuddy.sh
# Requires the codebuddy CLI on PATH. First launch prompts interactive login.
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_CLI=codebuddy
export ZC_INSTALL="Install it from https://www.codebuddy.ai/cli then try again."
exec sh "$(dirname "$0")/_launch.sh" wrapper.py codebuddy
