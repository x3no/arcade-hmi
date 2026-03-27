"""
Configuration loader for Arcade Control Panel
"""

import json
import os


class Config:
    """Application configuration"""
    
    # Default configuration
    DEFAULTS = {
        "pin": "1234",
        "gpio_power_pin": 17,
        "hid_device": "/dev/hidg0",
        "screen_width": 1920,
        "screen_height": 1080,
        "screen_timeout": 300,  # seconds
        "button_color": (50, 150, 255),
        "button_hover_color": (70, 170, 255),
        "button_text_color": (255, 255, 255),
        "bg_color": (0, 0, 0),
        "text_color": (255, 255, 255),
        "font_size": 32,
        "font_size_large": 48
    }
    
    def __init__(self, config_path='config/settings.json'):
        """
        Load configuration from JSON file
        
        Args:
            config_path: Path to configuration file
        """
        self.config = self.DEFAULTS.copy()
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    user_config = json.load(f)
                    self.config.update(user_config)
            except Exception as e:
                print(f"Error loading config: {e}, using defaults")
                
    def get(self, key, default=None):
        """Get configuration value"""
        return self.config.get(key, default)
        
    def __getitem__(self, key):
        """Allow dict-like access"""
        return self.config[key]
