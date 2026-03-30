"""
GPIO Controller for Raspberry Pi Zero 2
BCM pin mapping:
  GPIO 17 (pin 11) → Power button OUT  (optocoupler)
  GPIO 18 (pin 12) → Reset button  OUT  (optocoupler)
  GPIO 27 (pin 13) → Coin P1       IN   (optocoupler signal)
  GPIO 22 (pin 15) → Power LED     IN   (optocoupler signal)
  GPIO 23 (pin 16) → HDD LED       IN   (optocoupler signal)
  GPIO 24 (pin 18) → Coin P2       IN   (optocoupler signal)
"""

import time

try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    _GPIO_AVAILABLE = False
    print("[MOCK GPIO] RPi.GPIO not available, using mock")


class _MockGPIO:
    BCM      = 11
    OUT      = 0
    IN       = 1
    LOW      = 0
    HIGH     = 1
    PUD_UP   = 22
    PUD_DOWN = 21

    @staticmethod
    def setmode(m): pass
    @staticmethod
    def setup(pin, mode, pull_up_down=None): pass
    @staticmethod
    def output(pin, val): print(f"[MOCK GPIO] pin {pin} -> {val}")
    @staticmethod
    def input(pin): return 0
    @staticmethod
    def cleanup(): pass


if not _GPIO_AVAILABLE:
    GPIO = _MockGPIO()


# BCM pin assignments
PIN_POWER_BTN = 17
PIN_RESET_BTN = 18
PIN_COIN1     = 27
PIN_POWER_LED = 22
PIN_HDD_LED   = 23
PIN_COIN2     = 24


class GPIOController:
    """Control GPIO pins for power/reset buttons; read LED and coin inputs."""

    def __init__(self, power_pin=PIN_POWER_BTN):
        self.power_pin = power_pin

        GPIO.setmode(GPIO.BCM)

        # Outputs
        GPIO.setup(PIN_POWER_BTN, GPIO.OUT)
        GPIO.output(PIN_POWER_BTN, GPIO.LOW)
        GPIO.setup(PIN_RESET_BTN, GPIO.OUT)
        GPIO.output(PIN_RESET_BTN, GPIO.LOW)

        # Inputs (pull-down: signal is HIGH when optocoupler fires)
        GPIO.setup(PIN_POWER_LED, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(PIN_HDD_LED,   GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(PIN_COIN1,     GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(PIN_COIN2,     GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    # ── Outputs ──────────────────────────────────────────────────────────────

    def pulse_power_button(self, duration=0.2):
        print(f"Pulsing power button for {duration}s")
        GPIO.output(PIN_POWER_BTN, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(PIN_POWER_BTN, GPIO.LOW)

    def pulse_reset_button(self, duration=0.2):
        print(f"Pulsing reset button for {duration}s")
        GPIO.output(PIN_RESET_BTN, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(PIN_RESET_BTN, GPIO.LOW)

    def force_shutdown(self, duration=5):
        print(f"Force shutdown - holding power button for {duration}s")
        GPIO.output(PIN_POWER_BTN, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(PIN_POWER_BTN, GPIO.LOW)

    # ── Inputs ───────────────────────────────────────────────────────────────

    def read_power_led(self):
        """Return True when PC power LED is lit (optocoupler HIGH)."""
        return bool(GPIO.input(PIN_POWER_LED))

    def read_hdd_led(self):
        """Return True when HDD LED is active."""
        return bool(GPIO.input(PIN_HDD_LED))

    def read_coin1(self):
        """Return True while coin P1 optocoupler signal is HIGH."""
        return bool(GPIO.input(PIN_COIN1))

    def read_coin2(self):
        """Return True while coin P2 optocoupler signal is HIGH."""
        return bool(GPIO.input(PIN_COIN2))

    # ── Cleanup ──────────────────────────────────────────────────────────────

    def cleanup(self):
        GPIO.cleanup()

    def __del__(self):
        try:
            GPIO.cleanup()
        except Exception:
            pass
