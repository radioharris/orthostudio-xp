#!/bin/sh
# OSXP 8K test (Linux): run this file. It runs osxp_8k.py, beside it, with the programs of the
# OrthoStudio XP folder that install.sh added to the applications menu.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
ENTRY="${XDG_DATA_HOME:-$HOME/.local/share}/applications/orthostudio-xp.desktop"
PY=""
if [ -f "$ENTRY" ]; then
    EXE=$(sed -n 's/^Exec="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$ENTRY" | head -n 1)
    PY="$(dirname "$EXE")/python/bin/python3"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    echo "OrthoStudio XP was not found: run its install.sh once, then start this again."
else
    "$PY" osxp_8k.py "$@"
fi
