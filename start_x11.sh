#!/bin/bash
# Start application with X11

# Start X server and run the app
xinit /home/alberto/apps/upac/src/main.py -- :0 vt1 -nocursor
