#!/bin/bash
# Start application with X11 - use native display settings

# Start X server and run the app
xinit /usr/bin/python3 /home/dietpi/arcade-hmi/src/main.py -- :0 vt1 -nocursor
