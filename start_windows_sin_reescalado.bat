@echo off
REM Start application in native resolution for Windows (No scaling)
REM This runs the UI without forcing the 640x360 render scaling.

REM Add fullscreen flag without scaling to run at native resolution full screen
set ARCADE_FULLSCREEN=1

echo Starting Arcade HMI in native resolution...
python src\main.py
pause
