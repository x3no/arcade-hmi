"""
Keyboard mapper for arcade controls
Maps arcade actions to USB HID key codes
"""

from usb_hid import KeyCode


class ArcadeKeyMapper:
    """Map arcade actions to keyboard keys"""
    
    # Default key mapping for arcade machine
    KEYS = {
        # Coin buttons
        'coin_p1': KeyCode.KEY_5,      # Player 1 coin
        'coin_p2': KeyCode.KEY_6,      # Player 2 coin
        
        # Volume controls  
        'volume_up': KeyCode.KEY_VOLUME_UP,
        'volume_down': KeyCode.KEY_VOLUME_DOWN,
        'mute': KeyCode.KEY_MUTE,
        
        # Additional controls (customize as needed)
        'pause': KeyCode.KEY_P,
        'exit': KeyCode.KEY_ESC,
    }
    
    @staticmethod
    def get_key(action):
        """
        Get key code for action
        
        Args:
            action: Action name (e.g., 'coin_p1', 'volume_up')
            
        Returns:
            Key code or None if action not found
        """
        return ArcadeKeyMapper.KEYS.get(action)
