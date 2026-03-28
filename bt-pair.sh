#!/bin/bash
# First-time Bluetooth pairing for Arcade HID Keyboard.
# Run ONCE on the Raspberry Pi so the host PC can discover and pair with it.
# After pairing the host will reconnect automatically on subsequent boots.
#
# Usage: sudo ./bt-pair.sh

set -e

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root: sudo $0"
    exit 1
fi

TIMEOUT=120

echo "============================================"
echo "  Arcade HID Keyboard — Bluetooth Pairing  "
echo "============================================"
echo ""

# Ensure adapter is up and advertised as keyboard
hciconfig hci0 up          2>/dev/null || true
hciconfig hci0 class 0x002540 2>/dev/null || true   # Peripheral/Keyboard
hciconfig hci0 name "Arcade HID Keyboard" 2>/dev/null || true

# Configure BlueZ agent (NoInputNoOutput = no PIN needed)
bluetoothctl power on
bluetoothctl agent NoInputNoOutput
bluetoothctl default-agent
bluetoothctl discoverable on
bluetoothctl pairable on
bluetoothctl discoverable-timeout "$TIMEOUT"

echo ""
echo "The Pi is now visible as: 'Arcade HID Keyboard'"
echo ""
echo "On the HOST PC:"
echo "  1. Open Bluetooth settings"
echo "  2. Scan for new devices"
echo "  3. Select 'Arcade HID Keyboard' and click Pair"
echo "     (no PIN is required)"
echo ""
echo "Pairing window is open for ${TIMEOUT} seconds…"
echo "Press Ctrl+C to cancel."
echo ""

# Poll until a device appears in the paired list
END=$((SECONDS + TIMEOUT))
while [ $SECONDS -lt $END ]; do
    PAIRED=$(bluetoothctl paired-devices 2>/dev/null | head -n1)
    if [ -n "$PAIRED" ]; then
        MAC=$(echo "$PAIRED"  | awk '{print $2}')
        NAME=$(echo "$PAIRED" | cut -d' ' -f3-)
        echo ""
        echo "Paired with: $NAME  ($MAC)"
        # Trust the device so it auto-connects without prompts
        bluetoothctl trust "$MAC"
        bluetoothctl discoverable off
        bluetoothctl pairable off
        echo ""
        echo "Done! Restart bt-hid-server to allow the host to connect:"
        echo "  sudo systemctl restart bt-hid-server"
        exit 0
    fi
    sleep 2
done

echo ""
echo "Timeout reached — no pairing detected."
echo "Make sure Bluetooth is enabled on the host PC and try again."
bluetoothctl discoverable off
bluetoothctl pairable off
exit 1
