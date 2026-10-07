#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + Gemini wrapper (auto-approve mode)
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

export ZC_CLI=gemini
exec sh "$(dirname "$0")/_launch.sh" wrapper.py gemini -- --yolo
