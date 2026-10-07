#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + Codex wrapper
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_CLI=codex
exec sh "$(dirname "$0")/_launch.sh" wrapper.py codex
