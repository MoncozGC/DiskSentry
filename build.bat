@echo off
rem DiskSentry 一键打包：独立虚拟环境 + PyInstaller，产物 dist\DiskSentry.exe
cd /d "%~dp0"

if not exist .venv-build\Scripts\python.exe (
  echo [1/3] 创建打包虚拟环境...
  python -m venv .venv-build || goto :fail
  .venv-build\Scripts\python.exe -m pip install --upgrade pip pyinstaller || goto :fail
) else (
  echo [1/3] 虚拟环境已存在，跳过创建
)

echo [2/3] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 开始打包...
.venv-build\Scripts\pyinstaller.exe --noconfirm --clean DiskSentry.spec || goto :fail

echo.
echo 打包完成: %~dp0dist\DiskSentry.exe
exit /b 0

:fail
echo 打包失败，请检查上方错误信息
exit /b 1
