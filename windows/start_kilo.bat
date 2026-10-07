@echo off
REM ZeroCollab - starts server (if not running) + Kilo wrapper
REM Usage: start_kilo.bat [provider/model]
REM   e.g. start_kilo.bat anthropic/claude-sonnet-4-20250514
REM   Omit the model to use Kilo's configured default.
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set "ZC_CLI=kilo"
if "%~1"=="" (
    call "%~dp0_launch.bat" wrapper.py kilo
) else (
    call "%~dp0_launch.bat" wrapper.py kilo -- -m %1
)
