@echo off
REM Start application in full screen for Windows
REM This replicates the desktop behavior of start_x11.sh

REM ARCADE_FORCE_SCALE=1 forces the app to use the low-resolution render fallback
REM (640x360) with fullscreen scaling on high-resolution displays.
set ARCADE_FORCE_SCALE=1

REM Start the main script
python src\main.py
