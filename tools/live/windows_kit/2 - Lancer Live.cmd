@echo off
setlocal
set PYTHONUTF8=1
title OrthoStudio XP Live
call "%~dp0essai-windows\uv.cmd" || goto :end
cd /d "%~dp0OrthoStudio XP Live"
if not exist "%USERPROFILE%\.orthostudio\live.toml" (
  mkdir "%USERPROFILE%\.orthostudio" 2>nul
  > "%USERPROFILE%\.orthostudio\live.toml" echo enabled = true
)
echo La fenetre de Live s'ouvre (la premiere fois, uv prepare Python : une minute ou deux).
echo Laisse cette fenetre noire ouverte tant que tu voles.
uv run --all-extras python -m orthostudio.live ui
:end
echo.
pause
