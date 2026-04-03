#!/bin/bash
# Start application with X11
# On Raspberry Pi (console mode): launches its own X server
# On desktop Ubuntu: runs directly in the existing X session

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -n "$DISPLAY" ]]; then
    # Already inside an X session (desktop)
    echo "Desktop mode: running directly"
    # ARCADE_FORCE_SCALE=1 obliga a la app a usar el fallback de render a baja resolución
    # (640x360) con escalado a ventana completa en pantallas de alta resolución.
    export ARCADE_FORCE_SCALE=1
    python3 "$SCRIPT_DIR/src/main.py"
else
    # Console mode (Raspberry Pi).
    # --scale-from 640x360: VC4 maps the top-left 640x360 of the virtual
    # framebuffer to 1920x1080 in hardware. The app opens a borderless window
    # at (0,0) exactly filling that region — no CPU transform.scale needed.
    xinit /bin/bash -c "
        sleep 1
        # Encuentra la salida conectada (HDMI-1 o HDMI-A-1)
        OUT=\$(xrandr | grep -w 'connected' | cut -d ' ' -f1)
        if xrandr --output \$OUT --scale 0.333333x0.333333 2>/dev/null; then
            xrandr --output $OUT --panning 640x360; export ARCADE_HW_SCALE=1
        fi
        exec /usr/bin/python3 '$SCRIPT_DIR/src/main.py'
    " -- :0 vt1 -nocursor
fi
