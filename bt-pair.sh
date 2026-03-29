#!/bin/bash
# First-time Bluetooth pairing for Arcade HID Keyboard.
# Run ONCE on the Raspberry Pi so the host PC can discover and pair with it.
# After pairing the host will reconnect automatically on subsequent boots.
#
# Usage: sudo ./bt-pair.sh

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root: sudo $0"
    exit 1
fi

TIMEOUT=120

echo "============================================"
echo "  Arcade HID Keyboard — Bluetooth Pairing  "
echo "============================================"
echo ""

# Ensure dbus and bluetoothd are running (needed for bluetoothctl)
systemctl start dbus.service      2>/dev/null || true
systemctl start bluetooth.service 2>/dev/null || true
sleep 2

# Bring up adapter
hciconfig hci0 up          2>/dev/null || true
hciconfig hci0 class 0x002540    2>/dev/null || true
hciconfig hci0 name "Arcade HID Keyboard" 2>/dev/null || true

# Start bt-hid-server so L2CAP PSM 17/19 are listening and SDP record is registered
# Windows needs both to be ready BEFORE it tries to connect during pairing
echo "Starting bt-hid-server (opens L2CAP listeners for Windows to connect to)..."
systemctl start bt-hid-server 2>/dev/null || true
sleep 4   # give the server time to open sockets and register SDP record

if ! systemctl is-active --quiet bt-hid-server 2>/dev/null; then
    echo "WARNING: bt-hid-server could not start. Check:"
    echo "  sudo systemctl status bt-hid-server"
    echo "  sudo journalctl -u bt-hid-server -n 30"
    echo "Continuing anyway — pairing may fail."
else
    echo "bt-hid-server is running. SDP record registered."
fi
echo ""

# Open a single bluetoothctl session to register agent + set discoverable
bluetoothctl << 'BT_SETUP'
power on
agent NoInputNoOutput
default-agent
discoverable on
pairable on
discoverable-timeout 0
BT_SETUP

echo ""
echo "The Pi is now visible as: 'Arcade HID Keyboard'"
echo ""
echo "On the HOST PC:"
echo "  1. Open Bluetooth settings"
echo "  2. Scan for new devices"
echo "  3. Select 'Arcade HID Keyboard' and click Pair"
echo "     (no PIN is required)"
echo ""
echo "Pairing window is open for ${TIMEOUT} seconds..."
echo "Press Ctrl+C to cancel."
echo ""

# Poll until a device appears in the paired list
END=$((SECONDS + TIMEOUT))
while [ $SECONDS -lt $END ]; do
    PAIRED=$(bluetoothctl paired-devices 2>/dev/null | grep "^Device" | head -n1)
    if [ -n "$PAIRED" ]; then
        MAC=$(echo "$PAIRED"  | awk '{print $2}')
        NAME=$(echo "$PAIRED" | cut -d' ' -f3-)
        echo "Paired with: $NAME  ($MAC)"
        # Trust the device so it auto-connects without prompts each boot
        bluetoothctl << BT_TRUST
trust $MAC
discoverable off
pairable off
BT_TRUST
        echo ""
        echo "Done! Restarting bt-hid-server..."
        systemctl restart bt-hid-server 2>/dev/null || true
        echo "The host PC should now connect as a Bluetooth keyboard."
        exit 0
    fi
    printf "."
    sleep 3
done

echo ""
echo "Timeout reached — no pairing detected."
echo "Make sure Bluetooth is enabled on the host PC and try again."
echo "The Pi will REMAIN discoverable as 'Arcade HID Keyboard'."
echo "You can retry pairing from Windows without running this script again."
exit 1
