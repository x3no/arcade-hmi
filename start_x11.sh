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
    # Combine --fb (shrinks X11 virtual framebuffer to 640x360) with
    # --scale-from (VC4 upscales that 640x360 to 1920x1080 in hardware).
    # Result: pygame sees a 640x360 desktop and renders direct — no CPU scale.
    xinit /bin/bash -c "
        sleep 1
        if xrandr --fb 640x360 --output HDMI-1 --scale-from 640x360 2>/dev/null; then
            export ARCADE_HW_SCALE=1
        fi
        exec /usr/bin/python3 '$SCRIPT_DIR/src/main.py'
    " -- :0 vt1 -nocursor
fi
