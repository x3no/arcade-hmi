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
import queue
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
BT_STATUS_FILE = '/var/run/arcade-hid-status'  # 'connected' or 'disconnected'
CTRL_PSM       = 0x11   # L2CAP PSM 17 — HID Control
INTR_PSM       = 0x13   # L2CAP PSM 19 — HID Interrupt (key reports sent here)
BDADDR_ANY     = '00:00:00:00:00:00'

# D-Bus identifiers for BlueZ HID profile registration
DBUS_PROFILE_PATH = '/org/bluez/arcade_hid'       # HID control  PSM=17
INTR_PROFILE_PATH = '/org/bluez/arcade_hid_intr'  # HID interrupt PSM=19
HID_UUID          = '00001124-0000-1000-8000-00805f9b34fb'
INTR_UUID         = 'bdc4e400-0000-1000-8000-00805f9b34fb'  # custom — just needs to be unique

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

    # --- Report ID 3: Mouse ---
    0x05, 0x01, 0x09, 0x02, 0xa1, 0x01, 0x85, 0x03, 
    0x09, 0x01, 0xa1, 0x00, 0x05, 0x09, 0x19, 0x01, 
    0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 
    0x75, 0x01, 0x81, 0x02, 0x95, 0x01, 0x75, 0x05, 
    0x81, 0x03, 0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 
    0x95, 0x03, 0x81, 0x06, 0xc0, 0xc0
])

# HID report ID assignments
REPORT_ID_KEYBOARD  = 0x01
REPORT_ID_CONSUMER  = 0x02
REPORT_ID_MOUSE     = 0x03

# Release reports (one per report ID)
RELEASE_KEYBOARD = bytes([HID_INPUT, REPORT_ID_KEYBOARD, 0, 0, 0, 0, 0, 0, 0, 0])
RELEASE_CONSUMER = bytes([HID_INPUT, REPORT_ID_CONSUMER, 0x00, 0x00])
RELEASE_MOUSE    = bytes([HID_INPUT, REPORT_ID_MOUSE, 0x00, 0x00, 0x00, 0x00])

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


def setup_adapter(outbound_connect_fn=None):
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

    # Fix the ConfigurationDirectory mode mismatch that causes bluetoothd to
    # start in a degraded state.  systemd expects 0755 for ConfigurationDirectory
    # but sometimes the directory ends up with wrong permissions after an update.
    for _dir in ('/etc/bluetooth', '/var/lib/bluetooth'):
        try:
            current = oct(os.stat(_dir).st_mode & 0o777)
            os.chmod(_dir, 0o755)
            log.info(f'chmod 755 {_dir} (was {current})')
        except Exception as e:
            log.warning(f'chmod {_dir}: {e}')

    _diag(['hciconfig', 'hci0'])          # current adapter state
    _diag(['bluetoothctl', 'show'])       # BlueZ view of the adapter
    _diag(['systemctl', 'status', 'bluetooth', '--no-pager', '-l'])  # bluetoothd health

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

        # ── HID Profiles: registered WITH PSM so BlueZ routes L2CAP connections
        # to NewConnection — bypasses kernel security block on PSM<0x1001 in BlueZ 5.65+.

        class _CtrlProfile(dbus.service.Object):
            @dbus.service.method('org.bluez.Profile1', in_signature='', out_signature='')
            def Release(self): log.info('[CtrlProfile] Release')
            @dbus.service.method('org.bluez.Profile1', in_signature='oha{sv}', out_signature='')
            def NewConnection(self, path, fd, props):
                raw_fd = fd.take()
                log.info(f'[CtrlProfile] NewConnection fd={raw_fd} path={path}')
                try:
                    sock = socket.fromfd(raw_fd, socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET)
                    sock.setblocking(True)
                    self._queue.put(sock)
                    log.info('[CtrlProfile] ctrl socket queued — HIDP negotiation will begin')
                except Exception as e:
                    log.error(f'[CtrlProfile] fd wrap failed: {e}')
                finally:
                    try: os.close(raw_fd)
                    except: pass
            @dbus.service.method('org.bluez.Profile1', in_signature='o', out_signature='')
            def RequestDisconnection(self, path):
                log.info(f'[CtrlProfile] RequestDisconnection {path}')

        class _IntrProfile(dbus.service.Object):
            @dbus.service.method('org.bluez.Profile1', in_signature='', out_signature='')
            def Release(self): log.info('[IntrProfile] Release')
            @dbus.service.method('org.bluez.Profile1', in_signature='oha{sv}', out_signature='')
            def NewConnection(self, path, fd, props):
                raw_fd = fd.take()
                log.info(f'[IntrProfile] NewConnection fd={raw_fd} path={path}')
                try:
                    sock = socket.fromfd(raw_fd, socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET)
                    sock.setblocking(True)
                    self._queue.put(sock)
                    log.info('[IntrProfile] intr socket queued — keyboard ready soon')
                except Exception as e:
                    log.error(f'[IntrProfile] fd wrap failed: {e}')
                finally:
                    try: os.close(raw_fd)
                    except: pass
            @dbus.service.method('org.bluez.Profile1', in_signature='o', out_signature='')
            def RequestDisconnection(self, path):
                log.info(f'[IntrProfile] RequestDisconnection {path}')

        ctrl_profile = _CtrlProfile(bus, DBUS_PROFILE_PATH)
        ctrl_profile._queue = None  # set by BTKeyboardServer.run()
        intr_profile = _IntrProfile(bus, INTR_PROFILE_PATH)
        intr_profile._queue = None  # set by BTKeyboardServer.run()

        mgr = dbus.Interface(
            bus.get_object('org.bluez', '/org/bluez'),
            'org.bluez.ProfileManager1',
        )
        # Unregister stale profiles from any previous run
        for _p in (DBUS_PROFILE_PATH, INTR_PROFILE_PATH):
            try:
                mgr.UnregisterProfile(_p)
                log.info(f'Unregistered stale profile {_p}')
            except Exception:
                pass

        # Register ctrl profile WITH PSM=17 + full SDP record
        last_err = None
        for attempt in range(6):
            try:
                mgr.RegisterProfile(DBUS_PROFILE_PATH, HID_UUID, {
                    'ServiceRecord':         dbus.String(_SDP_RECORD),
                    'PSM':                   dbus.UInt16(CTRL_PSM),
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
            log.error(f'RegisterProfile (ctrl) failed after all retries: {last_err}')
            return None, None, None, None

        log.info(f'Registered ctrl profile HID UUID PSM={CTRL_PSM:#x}')

        # Register interrupt profile WITH PSM=19 (custom UUID, no SDP record)
        try:
            mgr.RegisterProfile(INTR_PROFILE_PATH, INTR_UUID, {
                'PSM':                   dbus.UInt16(INTR_PSM),
                'RequireAuthentication': dbus.Boolean(False),
                'RequireAuthorization':  dbus.Boolean(False),
                'AutoConnect':           dbus.Boolean(False),
            })
            log.info(f'Registered intr profile custom UUID PSM={INTR_PSM:#x}')
        except dbus.exceptions.DBusException as e:
            log.warning(f'RegisterProfile (intr) failed: {e}')

        log.info('BlueZ profile registration complete — connections will arrive via NewConnection')

        # Run the GLib event loop in a daemon thread to keep D-Bus alive
        loop = GLib.MainLoop()
        threading.Thread(target=loop.run, daemon=True).start()

        # ── D-Bus signal monitoring ──────────────────────────────────────────
        # Log EVERYTHING — no filtering — so we can see exactly what BlueZ
        # does (or does not do) when Windows tries to connect.

        def _trust_device(path):
            """Auto-trust a device so BlueZ doesn't silently reject it."""
            try:
                dev_obj  = bus.get_object('org.bluez', path)
                dev_props = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')
                dev_props.Set('org.bluez.Device1', 'Trusted', dbus.Boolean(True))
                log.info(f'[TRUST] Trusted device at {path}')
            except Exception as e:
                log.warning(f'[TRUST] Could not trust {path}: {e}')

        def _on_ifaces_added(path, ifaces):
            log.info(f'[DBUS] InterfacesAdded path={path} ifaces={list(ifaces.keys())}')
            if 'org.bluez.Device1' in ifaces:
                props = dict(ifaces['org.bluez.Device1'])
                log.info(f'[DBUS] Device1 ALL props: {props}')
                # Auto-trust so BlueZ doesn't refuse NewConnection silently
                threading.Thread(target=_trust_device, args=(path,), daemon=True).start()

        def _on_ifaces_removed(path, ifaces):
            log.info(f'[DBUS] InterfacesRemoved path={path} ifaces={ifaces}')

        def _on_props_changed(iface, changed, invalidated, sender=None):
            # Log ALL prop changes on ALL interfaces without filtering
            log.info(f'[DBUS] PropertiesChanged iface={iface} changed={dict(changed)} invalidated={list(invalidated)}')
            if iface == 'org.bluez.Device1' and 'Connected' in changed:
                connected = bool(changed['Connected'])
                log.info(f'[DBUS] >>> Device Connected={connected} <<<')
                if connected:
                    log.info('[DBUS] Windows is connected at ACL level — NewConnection should fire soon')
                    log.info(f'[DBUS] ctrl_queue size={ctrl_profile._queue.qsize() if ctrl_profile._queue else "no queue"}')
                    log.info(f'[DBUS] intr_queue size={intr_profile._queue.qsize() if intr_profile._queue else "no queue"}')
                    # Extract MAC from D-Bus path: /org/bluez/hci0/dev_04_7F_0E_02_33_03
                    if outbound_connect_fn and sender:
                        try:
                            mac = str(sender).split('/')[-1].replace('dev_', '').replace('_', ':')
                            if len(mac) == 17:  # looks like a valid MAC
                                log.info(f'[DBUS] Scheduling outbound reconnect attempt to {mac} in 4s')
                                t = threading.Timer(4.0, outbound_connect_fn, args=[mac])
                                t.daemon = True
                                t.start()
                        except Exception as e:
                            log.warning(f'[DBUS] Failed to schedule outbound connect: {e}')
                else:
                    log.info('[DBUS] Windows DISCONNECTED — if NewConnection was never called, BlueZ is not routing to our profiles')

        bus.add_signal_receiver(
            _on_ifaces_added,
            dbus_interface='org.freedesktop.DBus.ObjectManager',
            signal_name='InterfacesAdded',
        )
        bus.add_signal_receiver(
            _on_ifaces_removed,
            dbus_interface='org.freedesktop.DBus.ObjectManager',
            signal_name='InterfacesRemoved',
        )
        bus.add_signal_receiver(
            _on_props_changed,
            dbus_interface='org.freedesktop.DBus.Properties',
            signal_name='PropertiesChanged',
            path_keyword='sender',
        )
        log.info('D-Bus signal monitoring active (logging ALL events)')

        # Verify SDP record is actually visible by browsing our own MAC
        # ('sdptool browse local' is broken in BlueZ 5.x — use the real address)
        time.sleep(0.5)
        own_mac = str(adapter.Get('org.bluez.Adapter1', 'Address'))
        log.info(f'Verifying SDP via: sdptool browse {own_mac}')
        _diag(['sdptool', 'browse', own_mac])

        return mgr, loop, ctrl_profile, intr_profile

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
        self._ctrl_queue = queue.Queue()
        self._intr_queue = queue.Queue()

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

    def _try_outbound_connect(self, mac):
        """
        Called ~4s after Windows connects at ACL level if NewConnection never fired.
        The Pi proactively opens L2CAP connections to Windows' HID PSMs.
        In Bluetooth HID, either side can initiate. Windows often waits for the
        device to reconnect rather than opening channels itself.
        """
        # Don't bother if inbound connections already arrived
        if not self._ctrl_queue.empty() or self._intr is not None:
            log.info(f'[Reconnect] Inbound HID channels already present — skipping outbound to {mac}')
            return
        log.info(f'[Reconnect] No inbound HID channels after 4s — connecting outbound to {mac}')
        ctrl = None
        try:
            ctrl = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            ctrl.settimeout(10)
            ctrl.connect((mac, CTRL_PSM))
            ctrl.settimeout(None)
            log.info(f'[Reconnect] Outbound ctrl connected fd={ctrl.fileno()} to {mac}:{CTRL_PSM:#x}')
        except Exception as e:
            log.error(f'[Reconnect] Outbound ctrl connect to {mac}:{CTRL_PSM:#x} failed: {e}')
            if ctrl:
                try: ctrl.close()
                except: pass
            return
        time.sleep(0.3)
        try:
            intr = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            intr.settimeout(10)
            intr.connect((mac, INTR_PSM))
            intr.settimeout(None)
            log.info(f'[Reconnect] Outbound intr connected fd={intr.fileno()} to {mac}:{INTR_PSM:#x} — keyboard ready!')
            self._ctrl_queue.put(ctrl)
            self._intr_queue.put(intr)
        except Exception as e:
            log.error(f'[Reconnect] Outbound intr connect to {mac}:{INTR_PSM:#x} failed: {e}')
            try: ctrl.close()
            except: pass

    def _accept_connection(self):
        """
        Wait for BlueZ to deliver HID ctrl and intr sockets via D-Bus NewConnection,
        OR for outbound sockets placed in queues by _try_outbound_connect.
        """
        log.info('Waiting for BlueZ to deliver HID ctrl (PSM 0x11) from Windows...')
        ctrl = self._ctrl_queue.get()   # blocks until Windows connects on PSM 17
        log.info(f'[+] HID ctrl connected (fd={ctrl.fileno()})! Starting HIDP handler...')
        threading.Thread(target=self._ctrl_handler, args=(ctrl,), daemon=True).start()

        log.info('Waiting for BlueZ to deliver HID intr (PSM 0x13) from Windows...')
        try:
            intr = self._intr_queue.get(timeout=20)
            log.info(f'[+] HID intr connected (fd={intr.fileno()}) — keyboard ready!')
            return ctrl, intr
        except queue.Empty:
            log.warning('HID intr channel did not arrive within 20s — closing ctrl and retrying')
            try:
                ctrl.close()
            except Exception:
                pass
            raise OSError('HID intr channel timeout')
    def _write_status(self, connected: bool):
        try:
            with open(BT_STATUS_FILE, 'w') as f:
                f.write('connected' if connected else 'disconnected')
        except Exception:
            pass

    def _connection_loop(self):
        """Background thread: accept connections, update active sockets."""
        while True:
            ctrl = intr = None  # ensure finally block can always reference them
            try:
                ctrl, intr = self._accept_connection()
                with self._lock:
                    self._ctrl = ctrl
                    self._intr = intr
                self._write_status(True)
                log.info('[Status] HID connected — wrote status file')
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
                self._write_status(False)

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
                
                # Serialized sending over L2CAP socket with short delays
                # to ensure host can differentiate press and release events clearly
                intr.send(press)
                time.sleep(0.045)  # 45ms hold time (avoids host OS anti-bounce filters)
                intr.send(release)
                time.sleep(0.025)  # 25ms cool-down before next key
                return True
            except OSError as e:
                log.warning(f'send_key failed: {e}')
                self._intr = None
                self._ctrl = None
                return False

    def send_mouse(self, buttons: int, dx: int, dy: int, wheel: int = 0) -> bool:
        with self._lock:
            intr = self._intr
            if intr is None:
                return False
            try:
                # clamp dx, dy to signed 8-bit
                dx = max(-127, min(127, dx))
                dy = max(-127, min(127, dy))
                # python negative to uint8 conversion
                udx = dx & 0xFF
                udy = dy & 0xFF
                
                uwheel = wheel & 0xFF
                press = bytes([HID_INPUT, REPORT_ID_MOUSE, buttons & 0x07, udx, udy, uwheel])
                intr.send(press)
                
                # If buttons let go, immediately send releasing of buttons? Wait, a mouse movement could just be dx/dy, without clearing buttons!
                # We won't automatically release mouse buttons, but if it's a click, the client will send buttons=0 next!
                return True
            except OSError as e:
                log.warning(f'send_mouse failed: {e}')
                self._intr = None
                self._ctrl = None
                return False

    # ── Unix socket command server ────────────────────────────────────────────

    def _handle_unix_client(self, conn):
        try:
            with conn:
                data = conn.recv(6)
                if len(data) == 2:
                    # Legacy Keyboard
                    ok = self.send_key(data[0], data[1])
                    conn.send(b'\x01' if ok else b'\x00')
                elif (len(data) == 4 or len(data) == 5) and data[0] == 0x03:
                    # Mouse Report: [0x03, buttons, dx, dy] (dx, dy as signed byte sent as uint8)
                    buttons = data[1]
                    dx = data[2] if data[2] < 128 else data[2] - 256
                    dy = data[3] if data[3] < 128 else data[3] - 256
                    wheel = (data[4] if data[4] < 128 else data[4] - 256) if len(data) == 5 else 0
                    ok = self.send_mouse(buttons, dx, dy, wheel)
                    conn.send(b'\x01' if ok else b'\x00')
        except Exception:
            pass

    def run(self):
        bt_mgr, bt_loop, bt_ctrl_profile, bt_intr_profile = setup_adapter(
            outbound_connect_fn=self._try_outbound_connect,
        )
        # Wire the queues so D-Bus NewConnection puts sockets where _accept_connection waits
        if bt_ctrl_profile is not None:
            bt_ctrl_profile._queue = self._ctrl_queue
        if bt_intr_profile is not None:
            bt_intr_profile._queue = self._intr_queue

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
            if bt_mgr is not None:
                for _p in (DBUS_PROFILE_PATH, INTR_PROFILE_PATH):
                    try:
                        bt_mgr.UnregisterProfile(_p)
                        log.info(f'Profile {_p} unregistered')
                    except Exception as e:
                        log.warning(f'UnregisterProfile {_p}: {e}')
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
