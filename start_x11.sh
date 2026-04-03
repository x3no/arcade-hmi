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
    # Rotation is handled by display_rotate=1 in /boot/firmware/config.txt at the
    # KMS level, so no xrandr rotation is needed here.
    xinit /usr/bin/python3 "$SCRIPT_DIR/src/main.py" -- :0 vt1 -nocursor
fi
