@echo off
setlocal
set PYTHONUTF8=1
title OrthoStudio XP
call "%~dp0essai-windows\uv.cmd" || goto :end
cd /d "%~dp0OrthoStudio XP Live"
if not exist "%USERPROFILE%\.orthostudio\live.toml" (
  mkdir "%USERPROFILE%\.orthostudio" 2>nul
  > "%USERPROFILE%\.orthostudio\live.toml" echo enabled = true
)
echo L'app s'ouvre, dans une seule fenetre (la premiere fois, uv prepare Python : une minute ou deux).
echo Laisse cette fenetre noire ouverte tant que l'app est ouverte.
uv run --all-extras python -m orthostudio.desktop
:end
echo.
pause
