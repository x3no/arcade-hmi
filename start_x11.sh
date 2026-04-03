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
    # Console mode (Raspberry Pi).
    # xrandr --scale-from 640x360 tells VC4/KMS to upscale to 1920x1080 in
    # hardware — pygame renders at 640x360 with no CPU-side transform.scale.
    # sleep 1: wait for X11 to detect the monitor before running xrandr.
    xinit /bin/bash -c "
        sleep 1
        xrandr --output HDMI-1 --scale-from 640x360
        exec /usr/bin/python3 '$SCRIPT_DIR/src/main.py'
    " -- :0 vt1 -nocursor
fi
