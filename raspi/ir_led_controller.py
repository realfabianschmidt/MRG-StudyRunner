"""IR LED controller for NoIR camera illumination.

Controls IR LEDs via GPIO using gpiozero's PWMLED for duty-cycle limiting
(thermal protection). Reads Pi CPU temperature from the thermal zone sysfs
interface and shuts off LEDs when temperature exceeds the configured maximum.

Gracefully degrades when gpiozero is not available (development on Mac/Windows).
"""

from __future__ import annotations

from pathlib import Path


class IRLEDController:
    """Controls an IR LED bank on a single GPIO pin via PWM duty cycle."""

    def __init__(self, pin: int, duty_cycle: float = 0.8, max_temp_c: float = 70.0) -> None:
        self._pin = pin
        self._duty = min(1.0, max(0.0, duty_cycle))
        self._max_temp = max_temp_c
        self._enabled = False
        self._led = None
        self._available = False

        try:
            from gpiozero import PWMLED
            self._led = PWMLED(pin)
            self._available = True
            print(f"[IR_LED] GPIO {pin} initialised (duty={duty_cycle:.0%}, max_temp={max_temp_c}°C)")
        except ImportError:
            print("[IR_LED] gpiozero not available — IR LED control disabled (non-Pi environment)")
        except Exception as exc:
            print(f"[IR_LED] Could not initialise GPIO {pin}: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on(self) -> bool:
        """Turn the LED on at the configured duty cycle.

        Returns True if the LED was turned on, False if suppressed due to
        thermal protection or unavailability.
        """
        if not self._available or self._led is None:
            return False

        temp = self._read_cpu_temp()
        if temp >= self._max_temp:
            print(f"[IR_LED] Thermal protection triggered ({temp:.1f}°C >= {self._max_temp}°C) — LED off")
            self._led.off()
            self._enabled = False
            return False

        self._led.value = self._duty
        self._enabled = True
        return True

    def off(self) -> None:
        """Turn the LED off."""
        self._enabled = False
        if self._available and self._led is not None:
            self._led.off()

    def check_thermal(self) -> bool:
        """Check temperature and turn off LED if too hot.

        Returns True if LED remains on, False if turned off due to heat.
        Should be called periodically in the capture loop.
        """
        if not self._enabled:
            return False

        temp = self._read_cpu_temp()
        if temp >= self._max_temp:
            print(f"[IR_LED] Thermal check: {temp:.1f}°C >= {self._max_temp}°C — turning off")
            self.off()
            return False
        return True

    def shutdown(self) -> None:
        """Clean up GPIO resources on process exit."""
        self.off()
        if self._available and self._led is not None:
            try:
                self._led.close()
            except Exception:
                pass

    @property
    def is_on(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_cpu_temp(self) -> float:
        """Read Pi CPU temperature in °C from sysfs thermal zone."""
        try:
            raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text(encoding="ascii").strip()
            return int(raw) / 1000.0
        except Exception:
            return 0.0
