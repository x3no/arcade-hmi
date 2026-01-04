#!/bin/bash
# Start application with X11

# Start X server and run the app
xinit /usr/bin/python3 /home/dietpi/arcade-hmi/src/main.py -- :0 vt1 -nocursor
