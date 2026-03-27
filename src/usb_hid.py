"""
USB HID Keyboard Controller for Raspberry Pi Zero 2
Sends keyboard events to connected computer via /dev/hidg0
"""

NULL_CHAR = chr(0)


class USBHID:
    """Control USB HID keyboard device"""
    
    def __init__(self, device_path='/dev/hidg0'):
        """
        Initialize USB HID device
        
        Args:
            device_path: Path to HID gadget device (default: /dev/hidg0)
        """
        self.device_path = device_path
        self.device = None
        
    def __enter__(self):
        """Open HID device for writing"""
        try:
            self.device = open(self.device_path, 'rb+')
        except Exception as e:
            print(f"Error opening HID device {self.device_path}: {e}")
            raise
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close HID device"""
        if self.device:
            self.device.close()
            
    def write_report(self, modifiers, key_code):
        """
        Write HID report to device
        
        Args:
            modifiers: Modifier keys byte (Ctrl, Shift, Alt, etc.)
            key_code: Key code to send
        """
        # HID Report: [modifier, reserved, key1, key2, key3, key4, key5, key6]
        report = bytes([modifiers, 0, key_code, 0, 0, 0, 0, 0])
        if self.device:
            self.device.write(report)
            
    def release_all(self):
        """Release all keys"""
        report = bytes([0, 0, 0, 0, 0, 0, 0, 0])
        if self.device:
            self.device.write(report)
            
    def send_key(self, key_code, modifiers=0):
        """
        Send a single key press and release
        
        Args:
            key_code: HID key code
            modifiers: Modifier keys (default: 0)
        """
        self.write_report(modifiers, key_code)
        self.release_all()
        
    def send_combination(self, key_codes, modifiers=0):
        """
        Send a combination of keys
        
        Args:
            key_codes: List of key codes to press simultaneously
            modifiers: Modifier keys byte
        """
        if not key_codes:
            return
            
        # Press first key with modifiers
        self.write_report(modifiers, key_codes[0])
        # Release all
        self.release_all()


# USB HID Keyboard Scan Codes
class KeyCode:
    """USB HID Keyboard scan codes"""
    
    # Numbers
    KEY_1 = 0x1E
    KEY_2 = 0x1F
    KEY_3 = 0x20
    KEY_4 = 0x21
    KEY_5 = 0x22
    KEY_6 = 0x23
    KEY_7 = 0x24
    KEY_8 = 0x25
    KEY_9 = 0x26
    KEY_0 = 0x27
    
    # Letters
    KEY_A = 0x04
    KEY_B = 0x05
    KEY_C = 0x06
    KEY_D = 0x07
    KEY_E = 0x08
    KEY_F = 0x09
    KEY_G = 0x0A
    KEY_H = 0x0B
    KEY_I = 0x0C
    KEY_J = 0x0D
    KEY_K = 0x0E
    KEY_L = 0x0F
    KEY_M = 0x10
    KEY_N = 0x11
    KEY_O = 0x12
    KEY_P = 0x13
    KEY_Q = 0x14
    KEY_R = 0x15
    KEY_S = 0x16
    KEY_T = 0x17
    KEY_U = 0x18
    KEY_V = 0x19
    KEY_W = 0x1A
    KEY_X = 0x1B
    KEY_Y = 0x1C
    KEY_Z = 0x1D
    
    # Function keys
    KEY_F1 = 0x3A
    KEY_F2 = 0x3B
    KEY_F3 = 0x3C
    KEY_F4 = 0x3D
    KEY_F5 = 0x3E
    KEY_F6 = 0x3F
    KEY_F7 = 0x40
    KEY_F8 = 0x41
    KEY_F9 = 0x42
    KEY_F10 = 0x43
    KEY_F11 = 0x44
    KEY_F12 = 0x45
    
    # Special keys
    KEY_ENTER = 0x28
    KEY_ESC = 0x29
    KEY_BACKSPACE = 0x2A
    KEY_TAB = 0x2B
    KEY_SPACE = 0x2C
    
    # Arrow keys
    KEY_RIGHT = 0x4F
    KEY_LEFT = 0x50
    KEY_DOWN = 0x51
    KEY_UP = 0x52

    # Navigation
    KEY_INSERT = 0x49
    
    # Media keys (Consumer Control)
    KEY_MUTE = 0x7F
    KEY_VOLUME_UP = 0x80
    KEY_VOLUME_DOWN = 0x81


# Modifier keys
class Modifier:
    """USB HID Modifier key masks"""
    LEFT_CTRL = 0x01
    LEFT_SHIFT = 0x02
    LEFT_ALT = 0x04
    LEFT_GUI = 0x08  # Windows/Super key
    RIGHT_CTRL = 0x10
    RIGHT_SHIFT = 0x20
    RIGHT_ALT = 0x40
    RIGHT_GUI = 0x80
