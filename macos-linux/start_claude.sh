#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + Claude wrapper
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_CLI=claude
exec sh "$(dirname "$0")/_launch.sh" wrapper.py claude
