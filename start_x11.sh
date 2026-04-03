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
    # Try to use VC4 hardware scaler via xrandr --scale-from, so pygame renders
    # at 640x360 and VC4 upscales to 1920x1080 for free (no CPU transform.scale).
    # If xrandr succeeds, set ARCADE_HW_SCALE=1 so main.py renders direct.
    xinit /bin/bash -c "
        sleep 1
        if xrandr --output HDMI-1 --scale-from 640x360 2>/dev/null; then
            export ARCADE_HW_SCALE=1
        fi
        exec /usr/bin/python3 '$SCRIPT_DIR/src/main.py'
    " -- :0 vt1 -nocursor
fi
