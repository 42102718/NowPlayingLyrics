@echo off
rem Rebuild "Hummi.exe" into dist\  (needs: pip install -r requirements.txt pyinstaller)
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" tools\make_icon.py
"%PY%" -m PyInstaller --noconfirm --windowed --onedir --name "Hummi" --icon assets\icon.ico --paths app ^
  --add-data "web\index.html;web" --add-data "web\stage.html;web" --add-data "web\prompter.html;web" --add-data "web\queue.html;web" --add-data "web\folia_web;web\folia_web" --add-data "assets\icon.png;assets" --add-data "assets\icon_small.png;assets" --add-data "LICENSE;." --hidden-import playercap ^
  --collect-all winrt --collect-data opencc --collect-all webview --collect-all clr_loader ^
  --collect-all faster_whisper --collect-all ctranslate2 --collect-data soundcard ^
  --hidden-import pystray._win32 --hidden-import overlay --hidden-import notch --hidden-import battery --hidden-import audio_ctl --hidden-import viz --hidden-import devices --hidden-import tray_devices --hidden-import whatsnew --hidden-import hotkeys --hidden-import winchrome --hidden-import autopause --hidden-import screens --hidden-import wallpaper --hidden-import share --hidden-import chat --hidden-import songqueue --hidden-import folia_bridge --hidden-import halo_providers --collect-submodules halo_providers --hidden-import hid --collect-all libusb_package --hidden-import usb --hidden-import serial --hidden-import fonts --hidden-import ai_lyrics --hidden-import procloop --hidden-import jp_reading --hidden-import ko_reading --hidden-import translate --collect-all pykakasi ^
  --hidden-import soundcard --hidden-import comtypes --exclude-module nvidia --exclude-module onnxruntime --exclude-module torch app\server.py
if exist Hummi.spec del Hummi.spec
pause
