#!/usr/bin/env python3
"""
Bluetooth HID Keyboard server for Raspberry Pi Zero 2W.

Strategy:
  - Registers HID SDP record via BlueZ D-Bus ProfileManager1.RegisterProfile
    so Windows recognises the Pi as a keyboard during pairing.
  - Handles L2CAP connections with raw sockets on:
      PSM 17 (control)   — responds to HIDP SET_PROTOCOL / GET_PROTOCOL
      PSM 19 (interrupt) — sends 10-byte HID input reports
  - Accepts key commands from the arcade app via Unix socket
    /var/run/arcade-hid.sock   (2-byte request: [modifier, keycode])

Requirements:
  - bluetoothd running with -C (compat) flag  [configured by setup.sh]
  - python3-dbus, python3-gi                  [installed by setup.sh]
  - root privileges (L2CAP PSM < 0x1001 requires root)
"""

import os
import sys
import time
import socket
import subprocess
import threading
import logging
import signal

try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger('bt-hid')

UNIX_SOCK_PATH = '/var/run/arcade-hid.sock'
CTRL_PSM       = 0x11   # L2CAP PSM 17 — HID Control
INTR_PSM       = 0x13   # L2CAP PSM 19 — HID Interrupt (key reports sent here)
BDADDR_ANY     = '00:00:00:00:00:00'

# D-Bus identifiers for BlueZ HID profile registration
DBUS_PROFILE_PATH = '/org/bluez/arcade_hid'
HID_UUID          = '00001124-0000-1000-8000-00805f9b34fb'

# Bluetooth HID report
HID_INPUT  = 0xA1
REPORT_ID  = 0x01
RELEASE    = bytes([HID_INPUT, REPORT_ID, 0, 0, 0, 0, 0, 0, 0, 0])

# HIDP control channel message types (upper nibble of first byte)
HIDP_HANDSHAKE        = 0x00
HIDP_CONTROL          = 0x10
HIDP_GET_REPORT       = 0x40
HIDP_SET_REPORT       = 0x50
HIDP_GET_PROTOCOL     = 0x60
HIDP_SET_PROTOCOL     = 0x70
HIDP_DATA             = 0xA0
HIDP_VIRTUAL_UNPLUG   = 0x05

# HID report descriptor — standard keyboard with keycodes 0x00-0xFF
_HID_DESC = bytes([
    0x05, 0x01, 0x09, 0x06, 0xa1, 0x01,
    0x05, 0x07, 0x19, 0xe0, 0x29, 0xe7,
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01,
    0x95, 0x08, 0x81, 0x02, 0x95, 0x01,
    0x75, 0x08, 0x81, 0x03, 0x95, 0x05,
    0x75, 0x01, 0x05, 0x08, 0x19, 0x01,
    0x29, 0x05, 0x91, 0x02, 0x95, 0x01,
    0x75, 0x03, 0x91, 0x03, 0x95, 0x06,
    0x75, 0x08, 0x15, 0x00, 0x26, 0xff,
    0x00, 0x05, 0x07, 0x19, 0x00, 0x29,
    0xff, 0x81, 0x00, 0xc0,
])

# SDP record XML published via BlueZ D-Bus so Windows recognises a keyboard
_SDP_RECORD = f"""<?xml version="1.0" encoding="UTF-8"?>
<record>
  <attribute id="0x0001">
    <sequence><uuid value="0x1124"/></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100"/><uint16 value="0x0011"/></sequence>
      <sequence><uuid value="0x0011"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence><uuid value="0x1002"/></sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence><uuid value="0x1124"/><uint16 value="0x0100"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x000d">
    <sequence><sequence>
      <sequence><uuid value="0x0100"/><uint16 value="0x0013"/></sequence>
      <sequence><uuid value="0x0011"/></sequence>
    </sequence></sequence>
  </attribute>
  <attribute id="0x0100"><text value="Arcade HID Keyboard"/></attribute>
  <attribute id="0x0101"><text value="Arcade Control"/></attribute>
  <attribute id="0x0200"><uint16 value="0x0100"/></attribute>
  <attribute id="0x0201"><uint16 value="0x0111"/></attribute>
  <attribute id="0x0202"><uint8  value="0x40"/></attribute>
  <attribute id="0x0203"><uint8  value="0x00"/></attribute>
  <attribute id="0x0204"><boolean value="false"/></attribute>
  <attribute id="0x0205"><boolean value="false"/></attribute>
  <attribute id="0x0206">
    <sequence><sequence>
      <uint8 value="0x22"/>
      <text encoding="hex" value="{_HID_DESC.hex()}"/>
    </sequence></sequence>
  </attribute>
  <attribute id="0x020b"><uint16 value="0x0100"/></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80"/></attribute>
  <attribute id="0x020d"><boolean value="false"/></attribute>
  <attribute id="0x020e"><boolean value="false"/></attribute>
  <attribute id="0x020f"><uint16 value="0x0640"/></attribute>
  <attribute id="0x0210"><uint16 value="0x0320"/></attribute>
</record>"""


# ── Adapter and SDP setup via BlueZ D-Bus ────────────────────────────────────

def _diag(cmd):
    """Run a shell command and log its output as INFO lines."""
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=5)
        for line in out.strip().splitlines():
            log.info(f'  [diag] {line}')
    except Exception as e:
        log.info(f'  [diag] {" ".join(cmd)}: {e}')


def setup_adapter():
    """
    Configure BT adapter properties and publish the HID SDP record using
    BlueZ's ProfileManager1.RegisterProfile D-Bus API.

    Registering WITHOUT a PSM means BlueZ publishes the SDP record but does
    NOT intercept L2CAP connections — our raw sockets handle those instead.
    This is more reliable than sdptool, which fails on BlueZ 5.x.

    Returns (mgr, loop) so the caller can do a clean unregister on shutdown.
    Returns (None, None) on failure.
    """
    log.info('=== bt-hid-server starting ===')
    log.info(f'HAS_DBUS={HAS_DBUS}')
    _diag(['hciconfig', 'hci0'])          # current adapter state
    _diag(['bluetoothctl', 'show'])       # BlueZ view of the adapter

    if not HAS_DBUS:
        log.error('python3-dbus not available — cannot register SDP record')
        return None, None

    try:
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()

        # Set adapter properties via D-Bus (proper BlueZ API, not deprecated hciconfig)
        adapter_obj = bus.get_object('org.bluez', '/org/bluez/hci0')
        adapter = dbus.Interface(adapter_obj, 'org.freedesktop.DBus.Properties')

        # Also set the Device Class (0x002540 = Peripheral / Keyboard) via hciconfig
        # because org.bluez.Adapter1.Class is read-only in BlueZ 5.x
        try:
            subprocess.run(['hciconfig', 'hci0', 'class', '0x002540'], check=False)
            log.info('Device class set to 0x002540 (Peripheral/Keyboard)')
        except Exception as e:
            log.warning(f'hciconfig class: {e}')

        for prop, val in [
            ('Powered',             dbus.Boolean(True)),
            ('Discoverable',        dbus.Boolean(True)),
            ('Pairable',            dbus.Boolean(True)),
            ('DiscoverableTimeout', dbus.UInt32(0)),
            ('PairableTimeout',     dbus.UInt32(0)),
            ('Alias',               dbus.String('Arcade HID Keyboard')),
        ]:
            try:
                adapter.Set('org.bluez.Adapter1', prop, val)
                log.info(f'  adapter {prop} = {val}')
            except Exception as e:
                log.warning(f'  Set adapter {prop} FAILED: {e}')

        # Read back key properties to confirm they took effect
        for prop in ('Address', 'Alias', 'Discoverable', 'Pairable', 'Class'):
            try:
                v = adapter.Get('org.bluez.Adapter1', prop)
                log.info(f'  adapter readback {prop} = {v}')
            except Exception as e:
                log.warning(f'  readback {prop}: {e}')

        # Minimal Profile1 stub — BlueZ requires a D-Bus object at the profile path
        class _Profile(dbus.service.Object):
            @dbus.service.method('org.bluez.Profile1',
                                 in_signature='', out_signature='')
            def Release(self): pass

            @dbus.service.method('org.bluez.Profile1',
                                 in_signature='oha{sv}', out_signature='')
            def NewConnection(self, path, fd, props):
                # Without PSM in opts this shouldn't be called, but close the
                # fd if it is to avoid a file-descriptor leak.
                try:
                    os.close(fd.take())
                except Exception:
                    pass

            @dbus.service.method('org.bluez.Profile1',
                                 in_signature='o', out_signature='')
            def RequestDisconnection(self, path): pass

        _Profile(bus, DBUS_PROFILE_PATH)

        # RegisterProfile publishes the SDP record.
        # No PSM key → BlueZ registers SDP only; our raw sockets accept L2CAP.
        mgr = dbus.Interface(
            bus.get_object('org.bluez', '/org/bluez'),
            'org.bluez.ProfileManager1',
        )
        # Try to unregister first in case of a stale registration from a
        # previous crash (BlueZ cleans up asynchronously, may not be done yet).
        try:
            mgr.UnregisterProfile(DBUS_PROFILE_PATH)
            log.info('Unregistered stale HID profile')
        except Exception:
            pass  # not registered yet — fine

        # Retry loop: on a fast restart the old bus name might still be live
        # in BlueZ for a moment even after UnregisterProfile.
        last_err = None
        for attempt in range(6):
            try:
                mgr.RegisterProfile(DBUS_PROFILE_PATH, HID_UUID, {
                    'ServiceRecord':         dbus.String(_SDP_RECORD),
                    'RequireAuthentication': dbus.Boolean(False),
                    'RequireAuthorization':  dbus.Boolean(False),
                    'AutoConnect':           dbus.Boolean(False),
                })
                last_err = None
                break
            except dbus.exceptions.DBusException as e:
                last_err = e
                log.warning(f'RegisterProfile attempt {attempt+1}/6 failed: {e} — retrying in 2 s...')
                time.sleep(2)

        if last_err:
            log.error(f'RegisterProfile failed after all retries: {last_err}')
            return None, None

        log.info('HID SDP record registered via D-Bus — Windows will see keyboard profile')

        # Run the GLib event loop in a daemon thread to keep D-Bus alive
        loop = GLib.MainLoop()
        threading.Thread(target=loop.run, daemon=True).start()

        # Confirm SDP record is visible via sdptool
        time.sleep(0.5)
        log.info('SDP records on local device:')
        _diag(['sdptool', 'browse', 'local'])

        return mgr, loop

    except Exception as e:
        log.error(f'D-Bus adapter setup failed: {e}')
        import traceback
        log.error(traceback.format_exc())
        return None, None


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
        log.info('CTRL handler started')
        while True:
            try:
                data = ctrl.recv(64)
                if not data:          # graceful close
                    log.info('CTRL: empty read — host closed control channel')
                    break
                hex_data = data.hex()
                msg   = data[0]
                mtype = msg & 0xF0   # upper nibble = message type
                param = msg & 0x0F   # lower nibble = parameter

                if mtype == HIDP_SET_PROTOCOL:
                    mode = 'report' if param else 'boot'
                    log.info(f'CTRL RX SET_PROTOCOL({mode})  raw={hex_data}')
                    ctrl.send(bytes([HIDP_HANDSHAKE]))
                    log.info(f'CTRL TX HANDSHAKE(0x00)')

                elif mtype == HIDP_GET_PROTOCOL:
                    log.info(f'CTRL RX GET_PROTOCOL  raw={hex_data}')
                    ctrl.send(bytes([HIDP_DATA | 0x01]))
                    log.info(f'CTRL TX DATA(report_protocol=1)')

                elif mtype == HIDP_GET_REPORT:
                    log.info(f'CTRL RX GET_REPORT  raw={hex_data}')
                    ctrl.send(bytes([HIDP_HANDSHAKE]))
                    log.info(f'CTRL TX HANDSHAKE(0x00)')

                elif mtype == HIDP_SET_REPORT:
                    log.info(f'CTRL RX SET_REPORT  raw={hex_data}')
                    ctrl.send(bytes([HIDP_HANDSHAKE]))
                    log.info(f'CTRL TX HANDSHAKE(0x00)')

                elif mtype == HIDP_CONTROL:
                    if param == HIDP_VIRTUAL_UNPLUG:
                        log.info(f'CTRL RX VIRTUAL_CABLE_UNPLUG  raw={hex_data}')
                        break
                    else:
                        log.info(f'CTRL RX HIDP_CONTROL param={param:#x}  raw={hex_data}')

                else:
                    log.warning(f'CTRL RX unknown msg={msg:#04x} mtype={mtype:#x} param={param:#x}  raw={hex_data}')

            except OSError as e:
                log.info(f'CTRL: OSError — {e}')
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
        log.info(f'Opening L2CAP socket PSM {CTRL_PSM:#x} (control)...')
        ctrl_srv = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        log.info(f'Opening L2CAP socket PSM {INTR_PSM:#x} (interrupt)...')
        intr_srv = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        try:
            ctrl_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            intr_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ctrl_srv.bind((BDADDR_ANY, CTRL_PSM))
            log.info(f'Bound control socket to PSM {CTRL_PSM:#x}')
            intr_srv.bind((BDADDR_ANY, INTR_PSM))
            log.info(f'Bound interrupt socket to PSM {INTR_PSM:#x}')
            ctrl_srv.listen(1)
            intr_srv.listen(1)
            log.info('Both L2CAP sockets listening — waiting for host PC connection...')

            ctrl_client, addr = ctrl_srv.accept()
            log.info(f'Host connected on CTRL PSM {CTRL_PSM:#x} from {addr[0]}')
            # Start ctrl handler NOW so Windows doesn't timeout waiting for replies
            threading.Thread(
                target=self._ctrl_handler,
                args=(ctrl_client,),
                daemon=True,
            ).start()

            log.info(f'Waiting for host to open INTR PSM {INTR_PSM:#x}...')
            intr_client, intr_addr = intr_srv.accept()
            log.info(f'Host connected on INTR PSM {INTR_PSM:#x} from {intr_addr[0]} — keyboard ready!')
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
        bt_mgr, bt_loop = setup_adapter()

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
            # Explicitly unregister the HID profile so BlueZ cleans it up
            # immediately — prevents 'UUID already registered' on next start.
            if bt_mgr is not None:
                try:
                    bt_mgr.UnregisterProfile(DBUS_PROFILE_PATH)
                    log.info('HID profile unregistered cleanly')
                except Exception as e:
                    log.warning(f'UnregisterProfile on shutdown: {e}')
            if bt_loop is not None:
                try:
                    bt_loop.quit()
                except Exception:
                    pass
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
