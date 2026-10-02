@echo off
setlocal
set PYTHONUTF8=1
title OrthoStudio XP Live - les tests
call "%~dp0essai-windows\uv.cmd" || goto :end
cd /d "%~dp0OrthoStudio XP Live"
set "OSXP_LIVE_XPL_HOST=%~dp0essai-windows"
echo.
echo Les tests de Live tournent sur ce Windows : 5 a 10 minutes la premiere fois.
echo Une petite fenetre "Chargement de l'ortho" peut apparaitre devant une fenetre grise :
echo c'est un faux X-Plane, c'est normal. Le resultat va dans resultats-tests.txt.
echo.
uv run --all-extras python "%~dp0essai-windows\tests.py" > "%~dp0resultats-tests.txt" 2>&1
set "RESULT=%~dp0resultats-tests.txt"
powershell -NoProfile -Command "Get-Content -Tail 12 -Encoding UTF8 -LiteralPath $env:RESULT"
:end
echo.
pause
