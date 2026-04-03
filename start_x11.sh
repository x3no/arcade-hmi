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
    # --scale-from 640x360: VC4 maps the top-left 640x360 of the virtual
    # framebuffer to 1920x1080 in hardware. The app opens a borderless window
    # at (0,0) exactly filling that region — no CPU transform.scale needed.
    xinit /bin/bash -c "
        sleep 1
        if xrandr --output HDMI-1 --scale 3x3 2>/dev/null; then
            export ARCADE_HW_SCALE=1
        fi
        exec /usr/bin/python3 '$SCRIPT_DIR/src/main.py'
    " -- :0 vt1 -nocursor
fi
