@echo off
REM ZeroCollab - starts server (if not running) + MiniMax API agent wrapper
REM Usage: start_minimax.bat
REM Requires MINIMAX_API_KEY environment variable.
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

REM Check API key
if defined MINIMAX_API_KEY goto :launch
echo.
echo   Error: MINIMAX_API_KEY environment variable is not set.
echo   Get an API key at https://platform.minimax.io
echo   Then: set MINIMAX_API_KEY=your-key-here
echo.
pause
exit /b 1

:launch

call "%~dp0_launch.bat" wrapper_api.py minimax
