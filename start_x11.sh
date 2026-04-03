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
    # Console mode (Raspberry Pi) - start X server, force 1080p, 180° rotation
    xinit /bin/bash -c "
        xrandr --output HDMI-1 --mode 1920x1080 --rate 60 --rotate inverted 2>/dev/null || \
        xrandr --output HDMI-A-1 --mode 1920x1080 --rate 60 --rotate inverted 2>/dev/null || \
        xrandr --output HDMI-1 --mode 1920x1080 --rotate inverted 2>/dev/null || \
        xrandr --output HDMI-A-1 --mode 1920x1080 --rotate inverted 2>/dev/null || \
        xrandr --output HDMI-1 --rotate inverted 2>/dev/null || \
        xrandr --output HDMI-A-1 --rotate inverted 2>/dev/null || true
        exec /usr/bin/python3 '$SCRIPT_DIR/src/main.py'
    " -- :0 vt1 -nocursor
fi
