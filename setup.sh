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
apt-get install -y libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev
apt-get install -y libfreetype6-dev libjpeg-dev libportmidi-dev

# Install Python packages
echo "Installing Python packages..."
pip3 install -r requirements.txt

# Configure USB HID Gadget
echo "Configuring USB HID Gadget..."

# Detect boot partition path (DietPi Bookworm uses /boot/firmware)
if [ -f /boot/firmware/config.txt ]; then
    BOOT_PATH="/boot/firmware"
elif [ -f /boot/config.txt ]; then
    BOOT_PATH="/boot"
else
    echo "ERROR: Cannot find config.txt in /boot/firmware or /boot"
    exit 1
fi
echo "Using boot path: $BOOT_PATH"

# Add dwc2 overlay to config.txt if not present
if ! grep -q "dtoverlay=dwc2" "$BOOT_PATH/config.txt"; then
    echo "dtoverlay=dwc2" >> "$BOOT_PATH/config.txt"
fi

# Add modules-load=dwc2 to cmdline.txt (required for RPi Zero 2W USB gadget mode)
if ! grep -q "modules-load=dwc2" "$BOOT_PATH/cmdline.txt"; then
    # cmdline.txt must be a single line; append with sed
    sed -i 's/$/ modules-load=dwc2/' "$BOOT_PATH/cmdline.txt"
fi

# Add dwc2 and libcomposite to /etc/modules if not present
if ! grep -q "^dwc2" /etc/modules; then
    echo "dwc2" >> /etc/modules
fi
if ! grep -q "^libcomposite" /etc/modules; then
    echo "libcomposite" >> /etc/modules
fi

# Create USB HID gadget setup script
cat > /usr/local/bin/usb-gadget-hid << 'EOF'
#!/bin/bash
# Configure USB HID Keyboard Gadget
set -e

# Load required kernel modules
modprobe dwc2 || true
modprobe libcomposite || true

# Mount configfs if not already mounted
if ! mountpoint -q /sys/kernel/config; then
    mount -t configfs none /sys/kernel/config
fi

GADGET_DIR=/sys/kernel/config/usb_gadget/keyboard

# Clean up any existing gadget configuration
if [ -d "$GADGET_DIR" ]; then
    pushd "$GADGET_DIR" > /dev/null
    [ -s UDC ] && echo "" > UDC || true
    find configs/ -maxdepth 2 -type l -exec rm {} \; 2>/dev/null || true
    popd > /dev/null
    rmdir "$GADGET_DIR/configs/c.1/strings/0x409" 2>/dev/null || true
    rmdir "$GADGET_DIR/configs/c.1" 2>/dev/null || true
    rmdir "$GADGET_DIR/functions/hid.usb0" 2>/dev/null || true
    rmdir "$GADGET_DIR/strings/0x409" 2>/dev/null || true
    rmdir "$GADGET_DIR" 2>/dev/null || true
fi

mkdir -p "$GADGET_DIR"
cd "$GADGET_DIR"

# USB Identifiers
echo 0x1d6b > idVendor  # Linux Foundation
echo 0x0104 > idProduct # Multifunction Composite Gadget
echo 0x0100 > bcdDevice # v1.0.0
echo 0x0200 > bcdUSB    # USB2

# Strings
mkdir -p strings/0x409
echo "fedcba9876543210" > strings/0x409/serialnumber
echo "Arcade Control" > strings/0x409/manufacturer
echo "Arcade HID Keyboard" > strings/0x409/product

# Configuration
mkdir -p configs/c.1/strings/0x409
echo "Config 1: HID Keyboard" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# HID Keyboard Function
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length

# HID Report Descriptor (Keyboard, key codes 0x00-0xFF)
# Changed Logical/Usage Maximum from 0x65 (101) to 0xFF (255) so that
# key codes above 0x65 (e.g. Volume Up 0x80, Volume Down 0x81) are valid.
echo -ne \\x05\\x01\\x09\\x06\\xa1\\x01\\x05\\x07\\x19\\xe0\\x29\\xe7\\x15\\x00\\x25\\x01\\x75\\x01\\x95\\x08\\x81\\x02\\x95\\x01\\x75\\x08\\x81\\x03\\x95\\x05\\x75\\x01\\x05\\x08\\x19\\x01\\x29\\x05\\x91\\x02\\x95\\x01\\x75\\x03\\x91\\x03\\x95\\x06\\x75\\x08\\x15\\x00\\x26\\xff\\x00\\x05\\x07\\x19\\x00\\x29\\xff\\x81\\x00\\xc0 > functions/hid.usb0/report_desc

# Link function to configuration
ln -s functions/hid.usb0 configs/c.1/

# Activate gadget using the first available UDC
UDC_NAME=$(ls /sys/class/udc 2>/dev/null | head -n1)
if [ -z "$UDC_NAME" ]; then
    echo "ERROR: No UDC found. Verify dwc2 is loaded and the OTG USB port is used (not the PWR port)."
    exit 1
fi
echo "$UDC_NAME" > UDC
echo "USB HID gadget activated on UDC: $UDC_NAME"
EOF

chmod +x /usr/local/bin/usb-gadget-hid

# Create systemd service for USB gadget
cat > /etc/systemd/system/usb-gadget-hid.service << 'EOF'
[Unit]
Description=USB HID Gadget
After=local-fs.target
Before=arcade-control.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/usb-gadget-hid
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

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
systemctl enable usb-gadget-hid.service
systemctl enable arcade-control.service

# Disable unnecessary services for faster boot
echo "Optimizing boot time..."
systemctl disable bluetooth.service
systemctl disable hciuart.service
systemctl disable triggerhappy.service
systemctl disable avahi-daemon.service

# Configure boot options for faster startup
if ! grep -q "quiet" /boot/cmdline.txt; then
    sed -i '$ s/$/ quiet/' /boot/cmdline.txt
fi
if ! grep -q "splash" /boot/cmdline.txt; then
    sed -i '$ s/$/ splash/' /boot/cmdline.txt
fi
if ! grep -q "loglevel=3" /boot/cmdline.txt; then
    sed -i '$ s/$/ loglevel=3/' /boot/cmdline.txt
fi

# Disable console blanking
if ! grep -q "consoleblank=0" /boot/cmdline.txt; then
    sed -i '$ s/$/ consoleblank=0/' /boot/cmdline.txt
fi

# Configure framebuffer
echo "Configuring display..."
if ! grep -q "framebuffer_width" /boot/config.txt; then
    echo "framebuffer_width=800" >> /boot/config.txt
fi
if ! grep -q "framebuffer_height" /boot/config.txt; then
    echo "framebuffer_height=480" >> /boot/config.txt
fi

echo ""
echo "==================================="
echo "Setup Complete!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. Connect the Pi Zero 2 USB data port to the target PC"
echo "2. Reboot: sudo reboot"
echo "3. The application will start automatically"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u arcade-control.service -f"
echo ""
echo "To manually start/stop:"
echo "  sudo systemctl start arcade-control"
echo "  sudo systemctl stop arcade-control"
echo ""
