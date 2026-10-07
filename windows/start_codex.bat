@echo off
REM ZeroCollab - starts server (if not running) + Codex wrapper
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_CLI=codex"
call "%~dp0_launch.bat" wrapper.py codex
