#!/bin/bash
# Setup script for Arcade Control Panel on Raspberry Pi Zero 2

set -e

echo "==================================="
echo "Arcade Control Panel - Setup"
echo "==================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (sudo ./setup.sh)"
    exit 1
fi

# Update system
echo "Updating system..."
apt-get update
apt-get upgrade -y

# Install dependencies
echo "Installing dependencies..."
apt-get install -y python3 python3-pip python3-dev
apt-get install -y build-essential gcc
apt-get install -y libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev
apt-get install -y libfreetype6-dev libjpeg-dev libportmidi-dev

# Install pre-built system packages for heavy compiled dependencies
# (avoids building from source, which requires a full GCC cross-toolchain)
echo "Installing Python packages via apt (pre-built)..."
apt-get install -y python3-pygame python3-rpi.gpio || true

# Install remaining Python packages from pip (skip already-installed ones)
echo "Installing remaining Python packages via pip..."
pip3 install --break-system-packages \
    --ignore-installed pygame \
    -r requirements.txt 2>/dev/null || \
pip3 install --break-system-packages -r requirements.txt || true

# Configure Bluetooth HID
echo "Configuring Bluetooth HID (keyboard via Bluetooth to host PC)..."

# Install Bluetooth packages
apt-get install -y bluez bluez-tools python3-dbus python3-gi

# Configure BlueZ: advertise as HID keyboard, always discoverable
cat > /etc/bluetooth/main.conf << 'BTEOF'
[Policy]
AutoEnable=true

[General]
Class = 0x002540
DiscoverableTimeout = 0
PairableTimeout = 0
FastConnectable = true
Name = Arcade HID Keyboard
BTEOF

# Enable bluetoothd in compat mode so sdptool can register the HID SDP record
mkdir -p /etc/systemd/system/bluetooth.service.d/
BLUETOOTHD=$(command -v bluetoothd 2>/dev/null || echo /usr/lib/bluetooth/bluetoothd)
cat > /etc/systemd/system/bluetooth.service.d/compat.conf << BTEOF
[Service]
ExecStart=
ExecStart=${BLUETOOTHD} -C --noplugin=sap
BTEOF
# Reload and restart bluetooth so it picks up compat mode before bt-hid-server
systemctl daemon-reload
systemctl restart bluetooth.service
sleep 2

# Install BT HID server
cp src/bt_hid_server.py /usr/local/bin/bt-hid-server
chmod +x /usr/local/bin/bt-hid-server

# Create systemd service for BT HID server
cat > /etc/systemd/system/bt-hid-server.service << 'BTEOF'
[Unit]
Description=Bluetooth HID Keyboard Server
After=bluetooth.service
Wants=bluetooth.service
Before=arcade-control.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/bt-hid-server
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
BTEOF

# Copy application to /root/arcade-control
echo "Installing application..."
mkdir -p /root/arcade-control
cp -r src /root/arcade-control/
cp -r config /root/arcade-control/
cp requirements.txt /root/arcade-control/

# Install systemd service
echo "Installing systemd service..."
cp systemd/arcade-control.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable bluetooth.service
systemctl enable bt-hid-server.service
systemctl enable arcade-control.service

# Disable unnecessary services for faster boot (ignore if they don't exist)
echo "Optimizing boot time..."
for svc in hciuart.service triggerhappy.service avahi-daemon.service; do
    systemctl disable "$svc" 2>/dev/null || true
done

# Detect boot partition path (DietPi Bookworm uses /boot/firmware)
if [ -f /boot/firmware/config.txt ]; then
    BOOT_PATH="/boot/firmware"
elif [ -f /boot/config.txt ]; then
    BOOT_PATH="/boot"
else
    echo "WARNING: Cannot find config.txt; skipping boot options configuration"
    BOOT_PATH=""
fi

# Configure boot options for faster startup
if [ -n "$BOOT_PATH" ] && ! grep -q "quiet" "$BOOT_PATH/cmdline.txt"; then
    sed -i '$ s/$/ quiet/' "$BOOT_PATH/cmdline.txt"
fi
if [ -n "$BOOT_PATH" ] && ! grep -q "loglevel=3" "$BOOT_PATH/cmdline.txt"; then
    sed -i '$ s/$/ loglevel=3/' "$BOOT_PATH/cmdline.txt"
fi

# Disable console blanking
if [ -n "$BOOT_PATH" ] && ! grep -q "consoleblank=0" "$BOOT_PATH/cmdline.txt"; then
    sed -i '$ s/$/ consoleblank=0/' "$BOOT_PATH/cmdline.txt"
fi

# Configure framebuffer
echo "Configuring display..."
if [ -n "$BOOT_PATH" ] && ! grep -q "framebuffer_width" "$BOOT_PATH/config.txt"; then
    echo "framebuffer_width=800" >> "$BOOT_PATH/config.txt"
fi
if [ -n "$BOOT_PATH" ] && ! grep -q "framebuffer_height" "$BOOT_PATH/config.txt"; then
    echo "framebuffer_height=480" >> "$BOOT_PATH/config.txt"
fi

echo ""
echo "==================================="
echo "Setup Complete!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. Reboot: sudo reboot"
echo "2. After reboot, run the one-time Bluetooth pairing:"
echo "     sudo ./bt-pair.sh"
echo "3. Follow the instructions to pair from the host PC"
echo "4. The app will start automatically; the host auto-reconnects on boot"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u arcade-control.service -f"
echo ""
echo "To manually start/stop:"
echo "  sudo systemctl start arcade-control"
echo "  sudo systemctl stop arcade-control"
echo ""
