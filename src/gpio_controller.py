"""
GPIO Controller for Raspberry Pi Zero 2
Controls optocoupler for external PC power control
"""

import RPi.GPIO as GPIO
import time


class GPIOController:
    """Control GPIO pins for power button optocoupler"""
    
    def __init__(self, power_pin=17):
        """
        Initialize GPIO controller
        
        Args:
            power_pin: GPIO pin connected to optocoupler (default: GPIO17/pin 11)
        """
        self.power_pin = power_pin
        
        # Setup GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.power_pin, GPIO.OUT)
        GPIO.output(self.power_pin, GPIO.LOW)
        
    def pulse_power_button(self, duration=0.2):
        """
        Simulate power button press
        
        Args:
            duration: How long to hold the "button" (default: 0.2 seconds)
        """
        print(f"Pulsing power button for {duration}s")
        GPIO.output(self.power_pin, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(self.power_pin, GPIO.LOW)
        
    def force_shutdown(self, duration=5):
        """
        Force shutdown by holding power button
        
        Args:
            duration: How long to hold (default: 5 seconds for force shutdown)
        """
        print(f"Force shutdown - holding power button for {duration}s")
        GPIO.output(self.power_pin, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(self.power_pin, GPIO.LOW)
        
    def cleanup(self):
        """Clean up GPIO resources"""
        GPIO.cleanup()
        
    def __del__(self):
        """Cleanup on object destruction"""
        try:
            GPIO.cleanup()
        except:
            pass
