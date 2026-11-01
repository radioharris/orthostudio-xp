#!/bin/bash
# OSXP 8K test (macOS): double-click this file. It runs osxp_8k.py, beside it, with the programs
# of the OrthoStudio XP app installed on this Mac.
cd "$(dirname "$0")" || exit 1
PY=""
for app in "/Applications/OrthoStudio XP.app" "$HOME/Applications/OrthoStudio XP.app"; do
    if [ -x "$app/Contents/Resources/python/bin/python3" ]; then
        PY="$app/Contents/Resources/python/bin/python3"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "OrthoStudio XP was not found in Applications: install it first."
else
    "$PY" osxp_8k.py "$@"
fi
echo
read -r -n 1 -s -p "Press any key to close this window."
echo
