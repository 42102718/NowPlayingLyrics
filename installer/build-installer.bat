@echo off
rem Build release\NowPlayingLyrics-Setup-x.y.z.exe  (run ..\build.bat first so dist\ is up to date)
cd /d "%~dp0"
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 not found. Install it with:  winget install JRSoftware.InnoSetup
  pause
  exit /b 1
)
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" make_wizard_images.py
"%ISCC%" NowPlayingLyrics.iss
pause
