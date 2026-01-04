#!/bin/bash
# Start application with X11

# Create wrapper to add custom resolution and start app
cat > /tmp/start_app.sh << 'EOF'
#!/bin/bash
sleep 1

# Add 960x540 mode
xrandr --newmode "960x540_60.00" 34.96 960 992 1088 1216 540 541 544 555 -HSync +Vsync
xrandr --addmode HDMI-1 960x540_60.00 2>/dev/null || \
xrandr --addmode HDMI-2 960x540_60.00 2>/dev/null || \
xrandr --addmode HDMI-A-1 960x540_60.00 2>/dev/null

# Switch to 960x540
xrandr --output HDMI-1 --mode 960x540_60.00 2>/dev/null || \
xrandr --output HDMI-2 --mode 960x540_60.00 2>/dev/null || \
xrandr --output HDMI-A-1 --mode 960x540_60.00 2>/dev/null

# Start app
/usr/bin/python3 /home/dietpi/arcade-hmi/src/main.py
EOF

chmod +x /tmp/start_app.sh

# Start X server and run
xinit /tmp/start_app.sh -- :0 vt1 -nocursor
