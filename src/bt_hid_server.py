#!/usr/bin/env python3
"""
Bluetooth HID Keyboard server for Raspberry Pi Zero 2W.

Exposes the Pi as a Bluetooth HID keyboard to a host PC and accepts
key commands from the arcade app via a local Unix domain socket.

Requirements:
  - bluetoothd running with -C (compat) flag  [configured by setup.sh]
  - bluez-tools installed (hciconfig, sdptool) [installed by setup.sh]
  - root privileges (L2CAP PSM < 0x1001 requires root)

Protocol (Unix socket, path: /var/run/arcade-hid.sock):
  Client → Server : 2 bytes  [modifier_byte, keycode_byte]
  Server → Client : 1 byte   [0x01 = sent OK, 0x00 = host not connected]
"""

import os
import sys
import time
import socket
import threading
import subprocess
import logging
import signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger('bt-hid')

UNIX_SOCK_PATH = '/var/run/arcade-hid.sock'
CTRL_PSM       = 0x11   # L2CAP PSM 17 — HID Control
INTR_PSM       = 0x13   # L2CAP PSM 19 — HID Interrupt (key reports sent here)

# Bluetooth HID report
HID_INPUT  = 0xA1
REPORT_ID  = 0x01
RELEASE    = bytes([HID_INPUT, REPORT_ID, 0, 0, 0, 0, 0, 0, 0, 0])

# HIDP control channel message types (upper nibble of first byte)
HIDP_HANDSHAKE     = 0x00
HIDP_CONTROL       = 0x10
HIDP_GET_REPORT    = 0x40
HIDP_SET_REPORT    = 0x50
HIDP_GET_PROTOCOL  = 0x60
HIDP_SET_PROTOCOL  = 0x70
HIDP_DATA          = 0xA0

HIDP_HANDSHAKE_SUCCESS  = 0x00
HIDP_VIRTUAL_CABLE_UNPLUG = 0x05


# ── Adapter / SDP setup ──────────────────────────────────────────────────

def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()
        log.warning(f"{' '.join(cmd)} failed: {detail}")
    return r.returncode == 0


def setup_adapter():
    """Configure BT adapter as an HID keyboard and register SDP record."""
    _run(['hciconfig', 'hci0', 'up'])
    _run(['hciconfig', 'hci0', 'class', '0x002540'])   # Peripheral / Keyboard
    _run(['hciconfig', 'hci0', 'piscan'])               # discoverable + connectable
    _run(['hciconfig', 'hci0', 'name', 'Arcade HID Keyboard'])

    # Register HID SDP record.
    # Requires bluetoothd -C (compat mode) — /var/run/sdp must exist.
    # Wait up to 5 s for bluetoothd to create the socket.
    sdp_socket = '/var/run/sdp'
    for _ in range(10):
        if os.path.exists(sdp_socket):
            break
        log.info("Waiting for bluetoothd compat socket /var/run/sdp...")
        time.sleep(0.5)

    if not os.path.exists(sdp_socket):
        log.warning("bluetoothd compat socket not found — SDP record not registered. "
                    "Ensure bluetoothd runs with -C flag.")
    else:
        _run(['sdptool', 'del', '0x00010001'])
        if _run(['sdptool', 'add', '--handle=0x00010001', 'HID']):
            log.info("SDP HID record registered")
        else:
            log.warning("sdptool add HID failed — Windows may not recognise the keyboard profile")

    log.info("BT adapter configured as HID keyboard")


# ── Main server class ───────────────────────────────────────────────────

class BTKeyboardServer:

    def __init__(self):
        self._intr = None
        self._ctrl = None
        self._lock = threading.Lock()

    # ── HIDP control channel handler ──────────────────────────────────────────

    def _ctrl_handler(self, ctrl):
        """
        Handle HIDP messages from the host on the BT HID control channel.
        Must reply to SET_PROTOCOL / GET_PROTOCOL or Windows drops the link.
        Runs in its own thread so it doesn't block the interrupt channel.
        """
        while True:
            try:
                data = ctrl.recv(64)
                if not data:          # graceful close
                    break
                msg   = data[0]
                mtype = msg & 0xF0   # upper nibble = message type
                param = msg & 0x0F   # lower nibble = parameter

                if mtype == HIDP_SET_PROTOCOL:
                    # Windows sends this to switch between boot(0) and report(1) mode
                    log.info(f"SET_PROTOCOL {'report' if param else 'boot'}")
                    ctrl.send(bytes([HIDP_HANDSHAKE_SUCCESS]))

                elif mtype == HIDP_GET_PROTOCOL:
                    # Respond: we're in report protocol (1)
                    ctrl.send(bytes([HIDP_DATA | 0x01]))

                elif mtype in (HIDP_GET_REPORT, HIDP_SET_REPORT):
                    ctrl.send(bytes([HIDP_HANDSHAKE_SUCCESS]))

                elif mtype == HIDP_CONTROL:
                    if param == HIDP_VIRTUAL_CABLE_UNPLUG:
                        log.info("Virtual cable unplug")
                        break
                # other messages: silently ignore

            except OSError as e:
                log.info(f"Control channel closed: {e}")
                break

        # Signal disconnection by clearing the intr socket
        with self._lock:
            self._intr = None
            self._ctrl = None

    # ── L2CAP connection management ───────────────────────────────────────────

    def _accept_connection(self):
        """
        Open L2CAP listeners on PSM 17+19, block until host connects.
        Starts ctrl_handler immediately after ctrl connects so Windows
        does not time out waiting for HANDSHAKE while we wait for intr.
        """
        ctrl_srv = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        intr_srv = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        try:
            ctrl_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            intr_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ctrl_srv.bind(('', CTRL_PSM))
            intr_srv.bind(('', INTR_PSM))
            ctrl_srv.listen(1)
            intr_srv.listen(1)
            log.info("Waiting for Bluetooth HID connection from host PC…")

            ctrl_client, addr = ctrl_srv.accept()
            log.info(f"Host connected (control) from {addr[0]}")
            # Start ctrl handler NOW so Windows doesn't timeout waiting for replies
            threading.Thread(
                target=self._ctrl_handler,
                args=(ctrl_client,),
                daemon=True,
            ).start()

            intr_client, _ = intr_srv.accept()
            log.info("Host connected (interrupt) — keyboard ready")
            return ctrl_client, intr_client
        finally:
            ctrl_srv.close()
            intr_srv.close()

    def _connection_loop(self):
        """Background thread: accept connections, update active sockets."""
        while True:
            try:
                ctrl, intr = self._accept_connection()
                with self._lock:
                    self._ctrl = ctrl
                    self._intr = intr
                # Wait for intr socket to close (ctrl handled in its own thread)
                while True:
                    try:
                        data = intr.recv(1, socket.MSG_DONTWAIT)
                        if data == b'':   # graceful close
                            break
                    except BlockingIOError:
                        pass
                    except OSError:
                        break
                    time.sleep(0.5)
                log.info("Host disconnected; waiting for reconnect…")
            except Exception as e:
                log.error(f"Bluetooth error: {e}")
                time.sleep(3)
            finally:
                with self._lock:
                    self._ctrl = None
                    self._intr = None

    # ── Key sending ──────────────────────────────────────────────────────────

    def send_key(self, modifier: int, key_code: int) -> bool:
        with self._lock:
            intr = self._intr
        if intr is None:
            return False
        try:
            press = bytes([HID_INPUT, REPORT_ID, modifier, 0,
                           key_code, 0, 0, 0, 0, 0])
            intr.send(press)
            intr.send(RELEASE)
            return True
        except OSError as e:
            log.warning(f"Send failed: {e}")
            with self._lock:
                self._intr = None
                self._ctrl = None
            return False

    # ── Unix socket command server ────────────────────────────────────────────

    def _handle_unix_client(self, conn):
        try:
            with conn:
                data = conn.recv(2)
                if len(data) == 2:
                    ok = self.send_key(data[0], data[1])
                    conn.send(b'\x01' if ok else b'\x00')
        except Exception:
            pass

    def run(self):
        setup_adapter()

        threading.Thread(target=self._connection_loop, daemon=True).start()

        if os.path.exists(UNIX_SOCK_PATH):
            os.unlink(UNIX_SOCK_PATH)

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(UNIX_SOCK_PATH)
        os.chmod(UNIX_SOCK_PATH, 0o666)
        srv.listen(16)
        log.info(f"Unix socket ready at {UNIX_SOCK_PATH}")

        def _shutdown(sig, frame):
            log.info("Shutting down…")
            srv.close()
            if os.path.exists(UNIX_SOCK_PATH):
                os.unlink(UNIX_SOCK_PATH)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT,  _shutdown)

        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(
                    target=self._handle_unix_client,
                    args=(conn,),
                    daemon=True,
                ).start()
            except OSError:
                break


if __name__ == '__main__':
    BTKeyboardServer().run()

import os
import sys
import time
import socket
import threading
import subprocess
import logging
import signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger('bt-hid')

UNIX_SOCK_PATH = '/var/run/arcade-hid.sock'
CTRL_PSM       = 0x11   # L2CAP PSM 17 — HID Control
INTR_PSM       = 0x13   # L2CAP PSM 19 — HID Interrupt (key reports sent here)

# First byte of an HID INPUT report over Bluetooth
HID_INPUT  = 0xA1
REPORT_ID  = 0x01
# All-zeros release report
RELEASE    = bytes([HID_INPUT, REPORT_ID, 0, 0, 0, 0, 0, 0, 0, 0])


# ── Adapter / SDP setup ───────────────────────────────────────────────────────

def _run(cmd):
    subprocess.run(cmd, capture_output=True, check=False)


def setup_adapter():
    """Configure BT adapter as an HID keyboard and register SDP record."""
    _run(['hciconfig', 'hci0', 'up'])
    _run(['hciconfig', 'hci0', 'class', '0x002540'])   # Peripheral / Keyboard
    _run(['hciconfig', 'hci0', 'piscan'])               # discoverable + connectable
    _run(['hciconfig', 'hci0', 'name', 'Arcade HID Keyboard'])
    # Register a minimal HID SDP record so Windows/Linux recognise the device
    # as a keyboard instead of a generic BT device.
    # Requires bluetoothd -C (compat mode) — configured by setup.sh.
    _run(['sdptool', 'del', '0x00010001'])
    _run(['sdptool', 'add', '--handle=0x00010001', 'HID'])
    log.info("BT adapter configured as HID keyboard")


# ── Main server class ─────────────────────────────────────────────────────────

class BTKeyboardServer:

    def __init__(self):
        self._intr = None          # connected interrupt L2CAP socket
        self._ctrl = None          # connected control L2CAP socket
        self._lock = threading.Lock()

    # ── L2CAP connection management ───────────────────────────────────────────

    def _accept_connection(self):
        """Open L2CAP listeners on PSM 17+19, block until host connects."""
        ctrl_srv = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        intr_srv = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        try:
            ctrl_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            intr_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ctrl_srv.bind(('', CTRL_PSM))
            intr_srv.bind(('', INTR_PSM))
            ctrl_srv.listen(1)
            intr_srv.listen(1)
            log.info("Waiting for Bluetooth HID connection from host PC…")
            ctrl_client, addr = ctrl_srv.accept()
            log.info(f"Host connected (control) from {addr[0]}")
            intr_client, _   = intr_srv.accept()
            log.info("Host connected (interrupt) — keyboard ready")
            return ctrl_client, intr_client
        finally:
            ctrl_srv.close()
            intr_srv.close()

    def _connection_loop(self):
        """Background thread: accept connections, update active sockets."""
        while True:
            try:
                ctrl, intr = self._accept_connection()
                with self._lock:
                    self._ctrl = ctrl
                    self._intr = intr
                # Poll until the connection drops
                while True:
                    try:
                        ctrl.recv(1, socket.MSG_DONTWAIT)
                    except BlockingIOError:
                        pass    # no data — still connected
                    except OSError:
                        break   # disconnected
                    time.sleep(0.5)
                log.info("Host disconnected; waiting for reconnect…")
                with self._lock:
                    self._ctrl = None
                    self._intr = None
            except Exception as e:
                log.error(f"Bluetooth error: {e}")
                time.sleep(3)

    # ── Key sending ───────────────────────────────────────────────────────────

    def send_key(self, modifier: int, key_code: int) -> bool:
        with self._lock:
            intr = self._intr
        if intr is None:
            return False
        try:
            press = bytes([HID_INPUT, REPORT_ID, modifier, 0,
                           key_code, 0, 0, 0, 0, 0])
            intr.send(press)
            intr.send(RELEASE)
            return True
        except OSError as e:
            log.warning(f"Send failed: {e}")
            with self._lock:
                self._intr = None
                self._ctrl = None
            return False

    # ── Unix socket command server ────────────────────────────────────────────

    def _handle_unix_client(self, conn):
        try:
            with conn:
                data = conn.recv(2)
                if len(data) == 2:
                    ok = self.send_key(data[0], data[1])
                    conn.send(b'\x01' if ok else b'\x00')
        except Exception:
            pass

    def run(self):
        setup_adapter()

        # Start BT connection loop in background
        threading.Thread(target=self._connection_loop, daemon=True).start()

        # Clean up stale socket file
        if os.path.exists(UNIX_SOCK_PATH):
            os.unlink(UNIX_SOCK_PATH)

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(UNIX_SOCK_PATH)
        os.chmod(UNIX_SOCK_PATH, 0o666)
        srv.listen(16)
        log.info(f"Unix socket ready at {UNIX_SOCK_PATH}")

        def _shutdown(sig, frame):
            log.info("Shutting down…")
            srv.close()
            if os.path.exists(UNIX_SOCK_PATH):
                os.unlink(UNIX_SOCK_PATH)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT,  _shutdown)

        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(
                    target=self._handle_unix_client,
                    args=(conn,),
                    daemon=True,
                ).start()
            except OSError:
                break


if __name__ == '__main__':
    BTKeyboardServer().run()
