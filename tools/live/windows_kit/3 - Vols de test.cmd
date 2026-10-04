@echo off
setlocal
set PYTHONUTF8=1
title OrthoStudio XP Live - vols de test
call "%~dp0essai-windows\uv.cmd" || goto :end
cd /d "%~dp0OrthoStudio XP Live"
echo.
echo Les vols de test de la fenetre unique : X-Plane et l'app volent tout seuls, le meme vol
echo qu'au Mac (Toulouse, 20 min chacun). Quitte X-Plane et OrthoStudio XP avant : les vols les
echo demarrent eux-memes. Ne touche a rien pendant les vols.
echo.
echo   1 - la comparaison : Live seul dans l'app, puis Live avec OrthoStudio XP ouvert (55 min)
echo   2 - Live seul dans l'app (25 min)
echo   3 - Live avec OrthoStudio XP ouvert (25 min)
echo   4 - X-Plane seul, pour comparer (22 min)
echo   5 - tout : 1 puis 4 (1 h 20)
echo.
choice /c 12345 /m "Lequel"
set "WHICH=%ERRORLEVEL%"
uv run --all-extras python "%~dp0essai-windows\vol.py" %WHICH% "%~dp0."
:end
echo.
pause
