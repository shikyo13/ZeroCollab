#!/usr/bin/env sh
# ZeroCollab - starts server (if not running) + Antigravity (agy) wrapper
# Shared steps (CLI check, venv, server start and wait) live in _launch.sh.

# Pin agy's version - it self-updates and will otherwise drift out from under you
export AGY_CLI_DISABLE_AUTO_UPDATE=1

# agy adds itself to PATH via your shell profile at install time. A launcher run
# from a non-login shell (or a session opened before the install) may not have
# sourced that, so best-effort prepend the common install dirs if agy lives there.
for _agy_dir in "$HOME/.local/bin" "/usr/local/bin" "/opt/homebrew/bin" "$HOME/.agy/bin"; do
    if [ -x "$_agy_dir/agy" ]; then
        case ":$PATH:" in
            *":$_agy_dir:"*) ;;                 # already on PATH
            *) PATH="$_agy_dir:$PATH" ;;
        esac
    fi
done
export PATH

export ZC_CLI=agy
export ZC_CLI_LABEL="'agy' (Antigravity CLI)"
export ZC_INSTALL_NOTE="Note: run 'agy' once with no args to sign in before using it here."
exec sh "$(dirname "$0")/_launch.sh" wrapper.py antigravity
