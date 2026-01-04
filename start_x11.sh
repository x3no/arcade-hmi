#!/bin/bash
# Start application with X11

# Create wrapper script to set resolution and start app
cat > /tmp/start_app.sh << 'EOF'
#!/bin/bash
# Wait for X to be ready
sleep 1
# Force 960x540 resolution
xrandr --output HDMI-1 --mode 1920x1080 --scale 0.5x0.5 2>/dev/null || \
xrandr --output HDMI-2 --mode 1920x1080 --scale 0.5x0.5 2>/dev/null || \
xrandr --output HDMI-A-1 --mode 1920x1080 --scale 0.5x0.5 2>/dev/null
# Start the app
/usr/bin/python3 /home/dietpi/arcade-hmi/src/main.py
EOF

chmod +x /tmp/start_app.sh

# Start X server and run the app
xinit /tmp/start_app.sh -- :0 vt1 -nocursor
