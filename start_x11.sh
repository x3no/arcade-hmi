#!/bin/bash
# Start application with X11

# Set correct display resolution
export DISPLAY=:0

# Start X server with 1920x1080 resolution and run the app
xinit /usr/bin/python3 /home/dietpi/arcade-hmi/src/main.py -- :0 vt1 -nocursor -screen 0 1920x1080x24
