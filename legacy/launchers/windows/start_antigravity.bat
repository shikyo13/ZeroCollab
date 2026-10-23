@echo off
REM ZeroCollab - starts server (if not running) + Antigravity (agy) wrapper
REM Shared steps (venv, server start and wait, exit handling) live in _launch.bat.
setlocal

REM Pin agy's version - it self-updates and will otherwise drift out from under you
set AGY_CLI_DISABLE_AUTO_UPDATE=1

REM agy installs to %LOCALAPPDATA%\agy\bin and adds it to the *persistent* User
REM PATH - but a window opened before that install (e.g. Explorer double-click)
REM inherits a stale PATH and won't find agy. Prepend it so this always works.
if exist "%LOCALAPPDATA%\agy\bin\agy.exe" set "PATH=%LOCALAPPDATA%\agy\bin;%PATH%"

REM Always pause on exit so the window never vanishes silently, whether the wrapper
REM crashed or exited cleanly.

set "ZC_CLI=agy"
set "ZC_CLI_LABEL="agy" (Antigravity CLI)"
set "ZC_INSTALL_NOTE=Note: run "agy" once with no args to sign in before using it here."
set "ZC_EXIT=always"
set "ZC_EXIT_MESSAGE=Antigravity wrapper exited. Review the output above."
call "%~dp0_launch.bat" wrapper.py antigravity
