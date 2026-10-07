@echo off
REM ZeroCollab - starts server (if not running) + Gemini wrapper (auto-approve mode)
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_CLI=gemini"
call "%~dp0_launch.bat" wrapper.py gemini -- --yolo
