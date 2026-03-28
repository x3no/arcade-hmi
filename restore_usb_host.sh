#!/bin/bash
# Restores the USB OTG port to HOST mode so the touchscreen works again.
# Run this if the touchscreen stopped responding after setup.sh was executed.
# Must be run as root: sudo ./restore_usb_host.sh

set -e

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root: sudo $0"
    exit 1
fi

# Detect boot partition path
if [ -f /boot/firmware/config.txt ]; then
    BOOT_PATH="/boot/firmware"
elif [ -f /boot/config.txt ]; then
    BOOT_PATH="/boot"
else
    echo "ERROR: Cannot find config.txt"
    exit 1
fi
echo "Boot path: $BOOT_PATH"

# ── 1. Remove dwc2 overlay from config.txt ────────────────────────────────────
if grep -q "dtoverlay=dwc2" "$BOOT_PATH/config.txt"; then
    sed -i '/dtoverlay=dwc2/d' "$BOOT_PATH/config.txt"
    echo "[1/4] Removed dtoverlay=dwc2 from config.txt"
else
    echo "[1/4] dtoverlay=dwc2 not found in config.txt (already clean)"
fi

# ── 2. Remove modules-load=dwc2 from cmdline.txt ─────────────────────────────
if grep -q "modules-load=dwc2" "$BOOT_PATH/cmdline.txt"; then
    sed -i 's/ modules-load=dwc2//' "$BOOT_PATH/cmdline.txt"
    echo "[2/4] Removed modules-load=dwc2 from cmdline.txt"
else
    echo "[2/4] modules-load=dwc2 not found in cmdline.txt (already clean)"
fi

# ── 3. Remove dwc2 and libcomposite from /etc/modules ────────────────────────
if grep -q "^dwc2" /etc/modules; then
    sed -i '/^dwc2$/d' /etc/modules
    echo "[3/4] Removed dwc2 from /etc/modules"
else
    echo "[3/4] dwc2 not in /etc/modules (already clean)"
fi
if grep -q "^libcomposite" /etc/modules; then
    sed -i '/^libcomposite$/d' /etc/modules
    echo "      Removed libcomposite from /etc/modules"
fi

# ── 4. Disable and stop the HID gadget service ───────────────────────────────
if systemctl is-enabled usb-gadget-hid.service &>/dev/null; then
    systemctl disable usb-gadget-hid.service
    systemctl stop usb-gadget-hid.service 2>/dev/null || true
    echo "[4/4] Disabled usb-gadget-hid service"
else
    echo "[4/4] usb-gadget-hid service not enabled (already clean)"
fi

echo ""
echo "Done. Reboot to restore touchscreen:"
echo "  sudo reboot"
