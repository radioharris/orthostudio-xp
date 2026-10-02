@echo off
rem uv, which runs Live: found, or installed once asked (astral.sh's own installer, for this user).
where uv >nul 2>nul && exit /b 0
if exist "%USERPROFILE%\.local\bin\uv.exe" (
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  exit /b 0
)
echo uv, l'outil qui fait tourner Live, n'est pas installe sur ce PC.
choice /c ON /m "L'installer maintenant (depuis astral.sh, pour ce compte seulement)"
if errorlevel 2 exit /b 1
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 exit /b 1
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
