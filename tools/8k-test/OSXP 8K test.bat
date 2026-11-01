@echo off
rem OSXP 8K test (Windows): double-click this file, or drop tile folders onto it. It runs
rem osxp_8k.py, beside it, with the programs of the OrthoStudio XP installed for this user.
setlocal
set "PY=%LOCALAPPDATA%\Programs\OrthoStudio XP\python\python.exe"
if not exist "%PY%" (
  echo OrthoStudio XP was not found: install it first.
  pause
  exit /b 1
)
"%PY%" "%~dp0osxp_8k.py" %*
echo.
pause
