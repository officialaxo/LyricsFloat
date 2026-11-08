@echo off
setlocal enableextensions
cd /d "%~dp0"
title LyricsFloat build

echo ============================================
echo   LyricsFloat - build
echo ============================================

echo [1/4] Looking for Python...
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if defined PY goto :havepy

echo Python was not found. Trying to install Python 3.12 with winget...
where winget >nul 2>nul
if errorlevel 1 goto :nowinget
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :nowinget
echo.
echo Python 3.12 installed. Close this window and run build.bat again so PATH refreshes.
pause
exit /b 0

:nowinget
echo.
echo Could not install Python automatically.
echo Install Python 3.12 from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),
echo then run build.bat again.
pause
exit /b 1

:havepy
echo Using: %PY%

echo [2/4] Creating build environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul 2>nul

echo [3/4] Installing dependencies (PySide6, numpy, winsdk, PyAudioWPatch, PyInstaller)...
pip install PySide6 numpy pyinstaller
if errorlevel 1 goto :fail
set "WINFLAGS="
pip install winsdk
if errorlevel 1 goto :trywinrt
set "WINFLAGS=--collect-all winsdk"
goto :audio

:trywinrt
echo winsdk could not be installed for this Python, retrying with winrt packages...
pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Storage.Streams
if errorlevel 1 goto :fail
set "WINFLAGS=--collect-all winrt"

:audio
pip install PyAudioWPatch
if errorlevel 1 goto :noaudio
set "WINFLAGS=%WINFLAGS% --hidden-import pyaudiowpatch"
goto :icon
:noaudio
echo PyAudioWPatch failed - the audio visualizer will be unavailable in this build.

:icon
set "ICON="
python make_icon.py >nul 2>nul
if exist lf.ico set "ICON=--icon lf.ico"

echo [4/4] Building dist\LyricsFloat.exe (this takes a few minutes)...
pyinstaller --noconfirm --clean --onefile --windowed --name LyricsFloat %ICON% %WINFLAGS% ^
  --exclude-module tkinter ^
  --exclude-module PySide6.QtNetwork ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtWebEngineCore ^
  lyricsfloat.py
if errorlevel 1 goto :fail
if not exist "dist\LyricsFloat.exe" goto :fail

echo.
echo ============================================
echo   Done:  dist\LyricsFloat.exe
echo ============================================
pause
exit /b 0

:fail
echo.
echo Build failed - scroll up for the error.
pause
exit /b 1
