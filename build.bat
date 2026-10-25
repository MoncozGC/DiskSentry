@echo off
setlocal
rem DiskSentry one-click build: isolated venv + PyInstaller -^> dist\DiskSentry.exe
rem NOTE: keep this file ASCII-only with CRLF endings (cmd.exe mis-parses UTF-8/LF batch files)
cd /d "%~dp0"

if not exist ".venv-build\Scripts\python.exe" (
  echo [1/3] Creating build venv...
  python -m venv .venv-build || goto :fail
  ".venv-build\Scripts\python.exe" -m pip install --upgrade pip pyinstaller || goto :fail
) else (
  echo [1/3] Build venv found, skip creating
)

echo [2/3] Cleaning old build...
rem running instance locks the exe - stop it before rebuilding
taskkill /f /im DiskSentry.exe >nul 2>&1
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo [3/3] Building...
".venv-build\Scripts\pyinstaller.exe" --noconfirm --clean "%~dp0DiskSentry.spec" || goto :fail

echo.
echo Done: %~dp0dist\DiskSentry.exe
exit /b 0

:fail
echo Build failed. Check messages above.
exit /b 1
