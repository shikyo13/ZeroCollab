#!/usr/bin/env sh
# ZeroCollab - starts the server only
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_FOREGROUND=1
exec sh "$(dirname "$0")/_launch.sh" run.py
