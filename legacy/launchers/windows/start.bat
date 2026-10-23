@echo off
REM ZeroCollab - starts the server only
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_FOREGROUND=1"
set "ZC_EXIT=code"
call "%~dp0_launch.bat" run.py
