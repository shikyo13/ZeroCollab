@echo off
REM ZeroCollab - starts server (if not running) + Kimi wrapper
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_CLI=kimi"
call "%~dp0_launch.bat" wrapper.py kimi
