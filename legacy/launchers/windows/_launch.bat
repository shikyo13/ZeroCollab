@echo off
REM ZeroCollab - shared launcher behind the start_*.bat files in this folder.
REM
REM   call "%~dp0_launch.bat" <script.py> [args...]
REM
REM From the repository root: creates .venv and installs requirements on first run,
REM starts the server in its own window unless it is already listening, waits for it,
REM then runs "python <script.py> [args...]" (the wrapper, or run.py itself).
REM
REM Optional settings, set by the calling launcher inside its setlocal:
REM   ZC_CLI           CLI that must be on PATH before anything starts
REM   ZC_CLI_LABEL     how the missing-CLI error names it (default: "%ZC_CLI%")
REM   ZC_INSTALL       install hint shown with that error
REM   ZC_INSTALL_NOTE  optional second hint line
REM   ZC_FOREGROUND    1 = run the script itself as the server (no background server)
REM   ZC_EXIT          error (default): pause only if the script fails
REM                    always: pause on any exit, after ZC_EXIT_MESSAGE when set
REM                    code: report the exit code, then pause

cd /d "%~dp0.."

REM Seconds to wait for the background server before giving up
set "ZC_WAIT_SECONDS=60"

if not defined ZC_CLI goto :venv
where %ZC_CLI% >nul 2>&1
if not errorlevel 1 goto :venv
if not defined ZC_CLI_LABEL set "ZC_CLI_LABEL="%ZC_CLI%""
if not defined ZC_INSTALL set "ZC_INSTALL=Install it first, then try again."
echo.
echo   Error: %ZC_CLI_LABEL% was not found on PATH.
echo   %ZC_INSTALL%
if defined ZC_INSTALL_NOTE echo   %ZC_INSTALL_NOTE%
echo.
pause
exit /b 1

:venv
REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

if "%ZC_FOREGROUND%"=="1" goto :run

REM The port the server will listen on, from config.toml or the ZEROCOLLAB_PORT override (config.local.toml only adds agents)
set "ZC_PORT=8300"
for /f "delims=" %%p in ('python -c "from config_loader import load_config; print(load_config()['server']['port'])" 2^>nul') do set "ZC_PORT=%%p"

call :listening
if errorlevel 1 start "ZeroCollab server" cmd /c "python run.py"
set /a ZC_WAITED=0
:wait_server
call :listening
if not errorlevel 1 goto :run
if %ZC_WAITED% geq %ZC_WAIT_SECONDS% (
    echo.
    echo   Error: the server did not start listening on port %ZC_PORT% within %ZC_WAIT_SECONDS% seconds.
    echo   Check the "ZeroCollab server" window for errors.
    echo.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
set /a ZC_WAITED+=1
goto :wait_server

:run
python %*
set "ZC_STATUS=%errorlevel%"
if /i "%ZC_EXIT%"=="code" (
    echo.
    echo === Server exited with code %ZC_STATUS% ===
    pause
) else if /i "%ZC_EXIT%"=="always" (
    echo.
    if defined ZC_EXIT_MESSAGE call echo   %%ZC_EXIT_MESSAGE%%
    pause
) else if not "%ZC_STATUS%"=="0" (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
exit /b %ZC_STATUS%

REM errorlevel 0 when something is listening on ZC_PORT
:listening
netstat -ano | findstr /r /c:":%ZC_PORT% .*LISTENING" >nul 2>&1
exit /b
