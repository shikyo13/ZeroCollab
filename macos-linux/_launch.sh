#!/usr/bin/env sh
# ZeroCollab - shared launcher behind the start_*.sh files in this folder.
#
#   sh "$(dirname "$0")/_launch.sh" <script.py> [args...]
#
# From the repository root: creates (or recreates) .venv and installs requirements when
# needed, starts the server in a new terminal unless it is already listening, waits for
# it, then runs ".venv/bin/python <script.py> [args...]" (the wrapper, or run.py itself).
#
# Optional environment, exported by the calling launcher:
#   ZC_CLI           CLI that must be on PATH before anything starts
#   ZC_CLI_LABEL     how the missing-CLI error names it (default: "$ZC_CLI")
#   ZC_INSTALL       install hint shown with that error
#   ZC_INSTALL_NOTE  optional second hint line
#   ZC_FOREGROUND    1 = run the script itself as the server and report its exit code

cd "$(dirname "$0")/.." || exit 1

# Polls of the background server before giving up, and the pause between them
ZC_WAIT_TRIES=30
ZC_WAIT_INTERVAL=0.5
ZC_DEFAULT_PORT=8300

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Python 3 is required but was not found on PATH."
    exit 1
fi

if [ -n "$ZC_CLI" ] && ! command -v "$ZC_CLI" >/dev/null 2>&1; then
    echo ""
    echo "  Error: ${ZC_CLI_LABEL:-\"$ZC_CLI\"} was not found on PATH."
    echo "  ${ZC_INSTALL:-Install it first, then try again.}"
    [ -n "$ZC_INSTALL_NOTE" ] && echo "  $ZC_INSTALL_NOTE"
    echo ""
    exit 1
fi

if [ -d ".venv" ] && [ ! -x ".venv/bin/python" ]; then
    echo "Recreating .venv for this platform..."
    rm -rf .venv
fi
if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtual environment..."
    "$PYTHON_BIN" -m venv .venv || {
        echo "Error: failed to create .venv with $PYTHON_BIN."
        exit 1
    }
    .venv/bin/python -m pip install -q -r requirements.txt || {
        echo "Error: failed to install Python dependencies."
        exit 1
    }
fi

if [ "$ZC_FOREGROUND" = "1" ]; then
    .venv/bin/python "$@"
    code=$?
    echo ""
    echo "=== Server exited with code $code ==="
    exit "$code"
fi

# The port the server will listen on, after config.local.toml and environment overrides
ZC_PORT=$(.venv/bin/python -c "from config_loader import load_config; print(load_config()['server']['port'])" 2>/dev/null | tail -n 1)
case "$ZC_PORT" in
    ''|*[!0-9]*) ZC_PORT=$ZC_DEFAULT_PORT ;;
esac

is_server_running() {
    lsof -i :"$ZC_PORT" -sTCP:LISTEN >/dev/null 2>&1 || \
    ss -tlnp 2>/dev/null | grep -q ":$ZC_PORT "
}

if ! is_server_running; then
    if [ "$(uname -s)" = "Darwin" ]; then
        osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python run.py\"" > /dev/null 2>&1
    else
        if command -v gnome-terminal >/dev/null 2>&1; then
            gnome-terminal -- sh -c "cd '$(pwd)' && .venv/bin/python run.py; printf 'Press Enter to close... '; read _"
        elif command -v xterm >/dev/null 2>&1; then
            xterm -e sh -c "cd '$(pwd)' && .venv/bin/python run.py" &
        else
            .venv/bin/python run.py > data/server.log 2>&1 &
        fi
    fi

    i=0
    while [ "$i" -lt "$ZC_WAIT_TRIES" ]; do
        if is_server_running; then
            break
        fi
        sleep "$ZC_WAIT_INTERVAL"
        i=$((i + 1))
    done
fi

exec .venv/bin/python "$@"
