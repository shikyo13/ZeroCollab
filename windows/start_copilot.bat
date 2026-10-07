@echo off
REM ZeroCollab - starts server (if not running) + GitHub Copilot CLI wrapper
REM Usage: start_copilot.bat
REM Requires the copilot CLI on PATH. First launch prompts GitHub login.
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_CLI=copilot"
set "ZC_INSTALL=Install with: npm install -g @github/copilot"
set "ZC_INSTALL_NOTE=See https://github.com/github/copilot-cli for details."
call "%~dp0_launch.bat" wrapper.py copilot
