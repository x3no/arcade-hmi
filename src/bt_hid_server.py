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

# Bluetooth HID report header byte
HID_INPUT  = 0xA1
REPORT_ID  = 0x01   # kept for any legacy references
RELEASE    = bytes([HID_INPUT, REPORT_ID, 0, 0, 0, 0, 0, 0, 0, 0])  # legacy alias

# HIDP control channel message types (upper nibble of first byte)
HIDP_HANDSHAKE        = 0x00
HIDP_CONTROL          = 0x10
HIDP_GET_REPORT       = 0x40
HIDP_SET_REPORT       = 0x50
HIDP_GET_PROTOCOL     = 0x60
HIDP_SET_PROTOCOL     = 0x70
HIDP_DATA             = 0xA0
HIDP_VIRTUAL_UNPLUG   = 0x05

# HID report ID assignments
REPORT_ID_KEYBOARD  = 0x01
REPORT_ID_CONSUMER  = 0x02

# Consumer Control keycodes (Usage Page 0x0C) — same values as in usb_hid.py
CONSUMER_MUTE       = 0x7F   # KeyCode.KEY_MUTE
CONSUMER_VOL_UP     = 0x80   # KeyCode.KEY_VOLUME_UP  (note: non-standard mapping)
CONSUMER_VOL_DOWN   = 0x81   # KeyCode.KEY_VOLUME_DOWN
# Map internal keycodes → real HID Consumer usages (USB HID Usage Tables 1.3)
_CONSUMER_USAGE = {
    0x7F: 0x00E2,   # Mute
    0x80: 0x00E9,   # Volume Increment
    0x81: 0x00EA,   # Volume Decrement
}
_CONSUMER_KEYCODES = set(_CONSUMER_USAGE)

# HID report descriptor:
#   Report ID 1 — standard keyboard (modifier + reserved + 6 keys)
#   Report ID 2 — Consumer Control (16-bit usage, one key at a time)
_HID_DESC = bytes([
    # --- Report ID 1: Keyboard ---
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xa1, 0x01,        # Collection (Application)
    0x85, 0x01,        #   Report ID (1)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0xe0,        #   Usage Minimum (224)
    0x29, 0xe7,        #   Usage Maximum (231)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x08,        #   Report Count (8) — modifier byte
    0x81, 0x02,        #   Input (modifier keys)
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x08,        #   Report Size (8)
    0x81, 0x03,        #   Input (reserved)
    0x95, 0x05,        #   Report Count (5)
    0x75, 0x01,        #   Report Size (1)
    0x05, 0x08,        #   Usage Page (LEDs)
    0x19, 0x01,        #   Usage Minimum (1)
    0x29, 0x05,        #   Usage Maximum (5)
    0x91, 0x02,        #   Output (LED flags)
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x03,        #   Report Size (3)
    0x91, 0x03,        #   Output (LED padding)
    0x95, 0x06,        #   Report Count (6)
    0x75, 0x08,        #   Report Size (8)
    0x15, 0x00,        #   Logical Minimum (0)
    0x26, 0xff, 0x00,  #   Logical Maximum (255)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0x00,        #   Usage Minimum (0)
    0x29, 0xff,        #   Usage Maximum (255)
    0x81, 0x00,        #   Input (key array)
    0xc0,              # End Collection

    # --- Report ID 2: Consumer Control (media keys) ---
    0x05, 0x0c,        # Usage Page (Consumer)
    0x09, 0x01,        # Usage (Consumer Control)
    0xa1, 0x01,        # Collection (Application)
    0x85, 0x02,        #   Report ID (2)
    0x15, 0x00,        #   Logical Minimum (0)
    0x26, 0xff, 0x03,  #   Logical Maximum (1023)
    0x19, 0x00,        #   Usage Minimum (0)
    0x2a, 0xff, 0x03,  #   Usage Maximum (1023)
    0x75, 0x10,        #   Report Size (16)
    0x95, 0x01,        #   Report Count (1)
    0x81, 0x00,        #   Input (Consumer key)
    0xc0,              # End Collection
])

# Release reports (one per report ID)
RELEASE_KEYBOARD = bytes([HID_INPUT, REPORT_ID_KEYBOARD, 0, 0, 0, 0, 0, 0, 0, 0])
RELEASE_CONSUMER = bytes([HID_INPUT, REPORT_ID_CONSUMER, 0x00, 0x00])

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

        # ── Pairing agent ───────────────────────────────────────────────────
        # Register a NoInputNoOutput agent so BlueZ auto-accepts pairing
        # requests from Windows without requiring a PIN or confirmation.
        # Without this, BlueZ rejects the pairing with 'No agent is registered'.
        AGENT_PATH = '/org/bluez/arcade_agent'

        class _Agent(dbus.service.Object):
            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='', out_signature='')
            def Release(self):
                log.info('[Agent] Release called')

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='o', out_signature='s')
            def RequestPinCode(self, device):
                log.info(f'[Agent] RequestPinCode from {device} — returning empty')
                return ''

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='os', out_signature='')
            def DisplayPinCode(self, device, pincode):
                log.info(f'[Agent] DisplayPinCode {pincode} for {device}')

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='o', out_signature='u')
            def RequestPasskey(self, device):
                log.info(f'[Agent] RequestPasskey from {device} — returning 0')
                return dbus.UInt32(0)

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='ouq', out_signature='')
            def DisplayPasskey(self, device, passkey, entered):
                log.info(f'[Agent] DisplayPasskey {passkey} entered={entered} for {device}')

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='ou', out_signature='')
            def RequestConfirmation(self, device, passkey):
                log.info(f'[Agent] RequestConfirmation passkey={passkey} from {device} — auto-confirming')

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='o', out_signature='')
            def RequestAuthorization(self, device):
                log.info(f'[Agent] RequestAuthorization from {device} — auto-authorizing')

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='os', out_signature='')
            def AuthorizeService(self, device, uuid):
                log.info(f'[Agent] AuthorizeService uuid={uuid} from {device} — auto-authorizing')

            @dbus.service.method('org.bluez.Agent1',
                                 in_signature='', out_signature='')
            def Cancel(self):
                log.info('[Agent] Cancel called')

        _Agent(bus, AGENT_PATH)
        agent_mgr = dbus.Interface(
            bus.get_object('org.bluez', '/org/bluez'),
            'org.bluez.AgentManager1',
        )
        try:
            agent_mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass
        agent_mgr.RegisterAgent(AGENT_PATH, 'NoInputNoOutput')
        agent_mgr.RequestDefaultAgent(AGENT_PATH)
        log.info('[Agent] NoInputNoOutput agent registered as default — pairing will be auto-accepted')

        # ── HID Profile ─────────────────────────────────────────────────────
        # Minimal Profile1 stub — BlueZ requires a D-Bus object at the profile path
        class _Profile(dbus.service.Object):
            @dbus.service.method('org.bluez.Profile1',
                                 in_signature='', out_signature='')
            def Release(self): pass

            @dbus.service.method('org.bluez.Profile1',
                                 in_signature='oha{sv}', out_signature='')
            def NewConnection(self, path, fd, props):
                # This should NOT be called because we registered without a PSM
                # (BlueZ should NOT intercept our raw L2CAP sockets).
                # If it IS called it means BlueZ is routing the connection here
                # instead of to our raw sockets — that would explain why nothing works.
                raw_fd = fd.take()
                log.warning(
                    f'[Profile] NewConnection CALLED — BlueZ is intercepting L2CAP! '
                    f'path={path} fd={raw_fd} props={dict(props)} '
                    f'This is BAD — raw sockets will never see the connection.'
                )
                # Do NOT close the fd immediately; keep the connection alive
                # long enough for the ctrl_handler to negotiate HIDP.
                # Store it so the connection loop can pick it up.
                try:
                    import socket as _socket
                    conn = _socket.fromfd(raw_fd, _socket.AF_BLUETOOTH,
                                          _socket.SOCK_SEQPACKET)
                    os.close(raw_fd)   # fromfd dup'd it
                    log.warning('[Profile] Stored D-Bus fd as ctrl socket; keyboard MAY work via this path')
                    # Hand off to ctrl_handler if we have no ctrl yet
                    if self._server._ctrl is None:
                        threading.Thread(
                            target=self._server._ctrl_handler,
                            args=(conn,),
                            daemon=True,
                        ).start()
                        with self._server._lock:
                            self._server._ctrl = conn
                    else:
                        conn.close()
                except Exception as e:
                    log.error(f'[Profile] NewConnection fd handling failed: {e}')
                    try:
                        os.close(raw_fd)
                    except Exception:
                        pass

            @dbus.service.method('org.bluez.Profile1',
                                 in_signature='o', out_signature='')
            def RequestDisconnection(self, path):
                log.info(f'[Profile] RequestDisconnection path={path}')

        profile_obj = _Profile(bus, DBUS_PROFILE_PATH)
        profile_obj._server = None  # will be set by BTKeyboardServer after construction

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

        # Subscribe to BlueZ D-Bus signals to trace Windows connection attempts.
        # These fire even if the L2CAP connection never reaches our raw sockets.
        def _on_ifaces_added(path, ifaces):
            if 'org.bluez.Device1' in ifaces:
                props = ifaces['org.bluez.Device1']
                addr  = props.get('Address', '?')
                name  = props.get('Name', '?')
                log.info(f'[BT-EVENT] Device appeared: {addr} name={name} path={path}')

        def _on_props_changed(iface, changed, invalidated, sender=None):
            if iface == 'org.bluez.Device1':
                interesting = {'Connected', 'Paired', 'Trusted', 'Blocked',
                               'ServicesResolved', 'LegacyPairing'}
                filtered = {k: v for k, v in changed.items() if k in interesting}
                if filtered:
                    log.info(f'[BT-EVENT] Device1 props changed: {dict(filtered)}')
            elif iface == 'org.bluez.Adapter1':
                interesting = {'Discoverable', 'Pairable', 'Discovering'}
                filtered = {k: v for k, v in changed.items() if k in interesting}
                if filtered:
                    log.info(f'[BT-EVENT] Adapter1 props changed: {dict(filtered)}')

        bus.add_signal_receiver(
            _on_ifaces_added,
            dbus_interface='org.freedesktop.DBus.ObjectManager',
            signal_name='InterfacesAdded',
        )
        bus.add_signal_receiver(
            _on_props_changed,
            dbus_interface='org.freedesktop.DBus.Properties',
            signal_name='PropertiesChanged',
            path_keyword='sender',
        )
        log.info('D-Bus BlueZ signal monitoring active')

        # Verify SDP record is actually visible by browsing our own MAC
        # ('sdptool browse local' is broken in BlueZ 5.x — use the real address)
        time.sleep(0.5)
        own_mac = str(adapter.Get('org.bluez.Adapter1', 'Address'))
        log.info(f'Verifying SDP via: sdptool browse {own_mac}')
        _diag(['sdptool', 'browse', own_mac])

        return mgr, loop, bus, profile_obj

    except Exception as e:
        log.error(f'D-Bus adapter setup failed: {e}')
        import traceback
        log.error(traceback.format_exc())
        return None, None, None, None


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
            for _s in (ctrl_srv, intr_srv):
                _s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                ctrl_srv.bind((BDADDR_ANY, CTRL_PSM))
                log.info(f'Bound control socket to PSM {CTRL_PSM:#x}')
            except Exception as e:
                log.error(f'FAILED to bind control socket to PSM {CTRL_PSM:#x}: {e}')
                raise
            try:
                intr_srv.bind((BDADDR_ANY, INTR_PSM))
                log.info(f'Bound interrupt socket to PSM {INTR_PSM:#x}')
            except Exception as e:
                log.error(f'FAILED to bind interrupt socket to PSM {INTR_PSM:#x}: {e}')
                raise
            intr_srv.bind((BDADDR_ANY, INTR_PSM))
            log.info(f'Bound interrupt socket to PSM {INTR_PSM:#x}')
            ctrl_srv.listen(1)
            intr_srv.listen(1)
            log.info('Both L2CAP sockets listening — waiting for host PC connection...')

            log.info(f'Blocking on ctrl_srv.accept() PSM {CTRL_PSM:#x}...')
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
            ctrl = intr = None  # ensure finally block can always reference them
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
                # Explicitly close client sockets so PSM ports are freed for rebind
                for sock in (ctrl, intr):
                    if sock is not None:
                        try:
                            sock.close()
                        except Exception:
                            pass
                with self._lock:
                    self._ctrl = None
                    self._intr = None

    # ── Key sending ──────────────────────────────────────────────────────────

    def send_key(self, modifier: int, key_code: int) -> bool:
        with self._lock:
            intr = self._intr
        if intr is None:
            log.debug('send_key: no host connected')
            return False
        try:
            if key_code in _CONSUMER_KEYCODES:
                # Consumer Control report (ID=2): 16-bit usage little-endian
                usage = _CONSUMER_USAGE[key_code]
                press   = bytes([HID_INPUT, REPORT_ID_CONSUMER,
                                 usage & 0xFF, (usage >> 8) & 0xFF])
                release = RELEASE_CONSUMER
                log.info(f'TX Consumer key={key_code:#04x} usage={usage:#06x}')
            else:
                # Standard keyboard report (ID=1)
                press   = bytes([HID_INPUT, REPORT_ID_KEYBOARD,
                                 modifier, 0, key_code, 0, 0, 0, 0, 0])
                release = RELEASE_KEYBOARD
                log.info(f'TX Keyboard modifier={modifier:#04x} key={key_code:#04x}')
            intr.send(press)
            intr.send(release)
            return True
        except OSError as e:
            log.warning(f'send_key failed: {e}')
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
        bt_mgr, bt_loop, bt_bus, bt_profile = setup_adapter()
        if bt_profile is not None:
            bt_profile._server = self  # let NewConnection hand off to us

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
