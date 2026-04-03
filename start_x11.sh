#!/bin/bash
# Start application with X11
# On Raspberry Pi (console mode): launches its own X server
# On desktop Ubuntu: runs directly in the existing X session

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -n "$DISPLAY" ]]; then
    # Already inside an X session (desktop)
    echo "Desktop mode: running directly"
    python3 "$SCRIPT_DIR/src/main.py"
else
    # Console mode (Raspberry Pi) - start X server.
    # pygame renders at 640x360 (RS=1/3) and scales to 1920x1080 via
    # pygame.transform.scale each frame (SW, fast nearest-neighbour).
    xinit /usr/bin/python3 "$SCRIPT_DIR/src/main.py" -- :0 vt1 -nocursor
fi
