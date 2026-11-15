@echo off
rem Rebuild "Now Playing Lyrics.exe" into dist\  (needs: pip install -r requirements.txt pyinstaller)
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" make_icon.py
"%PY%" -m PyInstaller --noconfirm --windowed --onedir --name "Now Playing Lyrics" --icon icon.ico ^
  --add-data "index.html;." --add-data "icon.png;." ^
  --collect-all winrt --collect-data opencc --collect-all webview --collect-all clr_loader ^
  --collect-all faster_whisper --collect-all ctranslate2 --collect-data soundcard ^
  --hidden-import pystray._win32 --hidden-import overlay --hidden-import fonts --hidden-import ai_lyrics --hidden-import procloop ^
  --hidden-import soundcard --hidden-import comtypes --exclude-module nvidia --exclude-module onnxruntime --exclude-module torch server.py
pause
