@echo off
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" app\server.py --web
pause
