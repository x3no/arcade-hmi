"""
Bluetooth HID client for Arcade Control Panel.

Sends key commands to bt_hid_server.py via a local Unix domain socket.
API is intentionally compatible with USBHID so that main.py needs only
a one-line import change.

The USBHID alias at the bottom lets existing code like:
    with USBHID(self.config['hid_device']) as hid:
        hid.send_key(KeyCode.KEY_5)
work without any further modification.
"""

import socket

UNIX_SOCK_PATH = '/var/run/arcade-hid.sock'


class BluetoothHID:
    """
    Context-manager Bluetooth HID keyboard client.

    Each send_key() opens a short-lived connection to bt_hid_server,
    sends the keycode, and closes. bt_hid_server keeps the Bluetooth
    connection alive in the background.
    """

    def __init__(self, device_path=None):
        # device_path is ignored — kept for API compatibility with USBHID
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def _send(self, modifier: int, key_code: int):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect(UNIX_SOCK_PATH)
            sock.send(bytes([modifier & 0xFF, key_code & 0xFF]))
            resp = sock.recv(1)
            if resp != b'\x01':
                raise IOError("BT HID: host not connected")
        finally:
            sock.close()

    def send_key(self, key_code, modifiers=0):
        self._send(modifiers, key_code)

    def write_report(self, modifiers, key_code):
        self._send(modifiers, key_code)

    def release_all(self):
        # bt_hid_server always sends a key-release after each key press
        pass


# Backward-compatible alias: allows `with USBHID(device_path) as hid:` unchanged
USBHID = BluetoothHID
