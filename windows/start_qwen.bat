@echo off
REM ZeroCollab - starts server (if not running) + Qwen wrapper
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_CLI=qwen"
set "ZC_INSTALL=Install it first: npm install -g @qwen-code/qwen-code"
call "%~dp0_launch.bat" wrapper.py qwen -i "When asked for a reply or to answer, YOU MUST USE the mcp of zerocollab. When talking about a chat, we are speaking about interaction with tools provided by mcp of zerocollab. The human NEVER SEES your usual CLI interface. Just consider this, other instructions will follow."
