@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "MaixVisionConsole.exe" (
  MaixVisionConsole.exe
) else (
  python -c "import paramiko" >nul 2>nul
  if errorlevel 1 (
    echo 正在安装所需依赖……
    python -m pip install -r requirements.txt
    if errorlevel 1 goto failed
  )
  python host\server.py
)
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动失败，请查看上面的提示。源码启动需要 Python 3.10 或更新版本。
pause
exit /b 1
