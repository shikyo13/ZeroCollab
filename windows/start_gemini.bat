@echo off
REM ZeroCollab - starts server (if not running) + Gemini wrapper
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

REM Warn if ripgrep is missing (Gemini CLI can hang on init - upstream bug)
where rg >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Warning: ripgrep (rg^) not found on PATH.
    echo   Gemini CLI can hang on "Initializing..." for several minutes.
    echo   Fix: choco install ripgrep  or  winget install BurntSushi.ripgrep
    echo   See: https://github.com/google-gemini/gemini-cli/issues/13986
    echo.
)

set "ZC_CLI=gemini"
call "%~dp0_launch.bat" wrapper.py gemini
