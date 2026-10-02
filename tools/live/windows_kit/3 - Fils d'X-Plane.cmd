@echo off
setlocal
set PYTHONUTF8=1
title OrthoStudio XP Live - les fils d'X-Plane
call "%~dp0essai-windows\uv.cmd" || goto :end
cd /d "%~dp0OrthoStudio XP Live"
echo Les fils d'X-Plane, pendant 10 secondes...
uv run --all-extras python "%~dp0essai-windows\fils_xplane.py" > "%~dp0fils-xplane.txt" 2>&1
set "RESULT=%~dp0fils-xplane.txt"
powershell -NoProfile -Command "Get-Content -Encoding UTF8 -LiteralPath $env:RESULT"
:end
echo.
pause
