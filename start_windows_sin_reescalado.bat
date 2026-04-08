@echo off
REM Start application in native resolution for Windows (No scaling)
REM This runs the UI without forcing the 640x360 render scaling.

echo Starting Arcade HMI in native resolution...
python src\main.py
pause
