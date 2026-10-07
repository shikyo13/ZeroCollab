#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + Qwen wrapper
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_CLI=qwen
export ZC_INSTALL="Install it first: npm install -g @qwen-code/qwen-code"
exec sh "$(dirname "$0")/_launch.sh" wrapper.py qwen -i "When asked for a reply or to answer, YOU MUST USE the mcp of zerocollab. When talking about a chat, we are speaking about interaction with tools provided by mcp of zerocollab. The human NEVER SEES your usual CLI interface. Just consider this, other instructions will follow."
