@echo off
rem Rebuild "Hummi.exe" into dist\  (needs: pip install -r requirements.txt pyinstaller)
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" make_icon.py
"%PY%" -m PyInstaller --noconfirm --windowed --onedir --name "Hummi" --icon icon.ico ^
  --add-data "index.html;." --add-data "icon.png;." --add-data "icon_small.png;." --add-data "stage.html;." --add-data "folia_web;folia_web" --add-data "LICENSE;." --hidden-import playercap ^
  --collect-all winrt --collect-data opencc --collect-all webview --collect-all clr_loader ^
  --collect-all faster_whisper --collect-all ctranslate2 --collect-data soundcard ^
  --hidden-import pystray._win32 --hidden-import overlay --hidden-import notch --hidden-import battery --hidden-import audio_ctl --hidden-import viz --hidden-import devices --hidden-import tray_devices --hidden-import whatsnew --hidden-import hotkeys --hidden-import winchrome --hidden-import autopause --hidden-import screens --hidden-import wallpaper --hidden-import share --hidden-import chat --hidden-import folia_bridge --hidden-import halo_providers --collect-submodules halo_providers --hidden-import hid --collect-all libusb_package --hidden-import usb --hidden-import serial --hidden-import fonts --hidden-import ai_lyrics --hidden-import procloop ^
  --hidden-import soundcard --hidden-import comtypes --exclude-module nvidia --exclude-module onnxruntime --exclude-module torch server.py
pause
