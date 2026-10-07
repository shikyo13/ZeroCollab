@echo off
REM ZeroCollab - starts server (if not running) + CodeBuddy wrapper
REM Usage: start_codebuddy.bat
REM Requires the codebuddy CLI on PATH. First launch prompts interactive login.
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_CLI=codebuddy"
set "ZC_INSTALL=Install it from https://www.codebuddy.ai/cli then try again."
call "%~dp0_launch.bat" wrapper.py codebuddy
