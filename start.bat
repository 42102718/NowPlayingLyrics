@echo off
cd /d "%~dp0"
rem 用實際安裝的 Python (WindowsApps 的捷徑別名不可靠)
set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" app\server.py
