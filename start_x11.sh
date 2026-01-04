#!/bin/bash
# Start application with X11

# Create minimal xorg.conf for 1920x1080
cat > /tmp/xorg.conf << EOF
Section "Monitor"
    Identifier "Monitor0"
EndSection

Section "Screen"
    Identifier "Screen0"
    Monitor "Monitor0"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1920x1080"
    EndSubSection
EndSection
EOF

# Start X server and run the app
xinit /usr/bin/python3 /home/dietpi/arcade-hmi/src/main.py -- :0 vt1 -nocursor -config /tmp/xorg.conf
