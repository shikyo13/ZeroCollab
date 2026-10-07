@echo off
REM ZeroCollab - starts server (if not running) + API agent wrapper
REM Usage: start_api_agent.bat <agent_name>
REM Example: start_api_agent.bat qwen
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

set AGENT_NAME=%~1
if "%AGENT_NAME%"=="" (
    echo.
    echo   ZeroCollab - API Agent Launcher
    echo   ---------------------------------
    echo   Enter the agent name from your config.local.toml
    echo   Example: qwen, mistral, llama, deepseek
    echo.
    set /p AGENT_NAME="  Agent name: "
)
if "%AGENT_NAME%"=="" (
    echo   Error: No agent name provided.
    pause
    exit /b 1
)

set "ZC_EXIT=always"
call "%~dp0_launch.bat" wrapper_api.py %AGENT_NAME%
