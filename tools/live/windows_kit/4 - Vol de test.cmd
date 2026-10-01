@echo off
setlocal
set PYTHONUTF8=1
title OrthoStudio XP Live - vol de test
call "%~dp0essai-windows\uv.cmd" || goto :end
cd /d "%~dp0OrthoStudio XP Live"
echo.
echo Un vol de test : X-Plane vole tout seul, le meme vol qu'au Mac (Toulouse, 20 min).
echo Quitte X-Plane et ferme la fenetre de Live avant : le vol les demarre lui-meme.
echo Ne touche pas a X-Plane pendant le vol.
echo.
echo   1 - avec Live (ZL16/ZL18, a froid : rien de construit avant)
echo   2 - X-Plane seul, sans Live (pour comparer)
echo   3 - les deux, l'un apres l'autre (45 min)
echo.
choice /c 123 /m "Lequel"
set "WHICH=%ERRORLEVEL%"
uv run --all-extras python "%~dp0essai-windows\vol.py" %WHICH% "%~dp0."
:end
echo.
pause
