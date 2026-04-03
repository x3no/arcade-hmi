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
    # Try --scale 3x3 so VC4/Pixman upscales the top-left 640x360 of the
    # virtual FB to fill the 1920x1080 physical screen. pygame then renders
    # a borderless 640x360 window at (0,0) — no CPU transform.scale needed.
    # If xrandr fails we reset scale to 1:1 so there is no double-scaling.
    xinit /bin/bash -c "
        export DISPLAY=:0
        sleep 1
        if xrandr --output HDMI-1 --scale-from 640x360 2>/dev/null; then
            export ARCADE_HW_SCALE=1
        else
            xrandr --output HDMI-1 --scale 1x1 2>/dev/null || true
        fi
        exec /usr/bin/python3 '$SCRIPT_DIR/src/main.py'
    " -- :0 vt1 -nocursor
fi
