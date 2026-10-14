import logging
import asyncio
import math

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.util.percentage import percentage_to_ranged_value
from homeassistant.util.scaling import int_states_in_range
from .entity import SmarteefiEntity, build_entities

SPEED_RANGE = (1, 4)
SPEED_TO_PERCENTAGE = {0: 0, 1: 25, 2: 50, 3: 75, 4: 100}

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Smarteefi fans from a config entry."""
    async_add_entities(build_entities(entry, "fan", SmarteefiFan))

class SmarteefiFan(SmarteefiEntity, FanEntity):
    """Representation of a Smarteefi fan."""

    _attr_supported_features = (
        FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF | FanEntityFeature.SET_SPEED
    )
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, device, hacli_path, ip_address, netmask):
        super().__init__(device, hacli_path, ip_address, netmask, "Unnamed Fan")
        self._state = False  # Assume the fan is off by default
        self._speed = 0

    def _apply_status(self, status):
        previous = (self._state, self._speed)

        # Speed flags: r1=0x10, r2=0x20, r3=0x40
        if status & 0x40:
            speed = 4
        elif status & 0x20 and status & 0x10:
            speed = 3
        elif status & 0x20:
            speed = 2
        elif status & 0x10:
            speed = 1
        else:
            # Status 0, or no speed flags set: treat as off
            speed = 0

        self._speed = speed
        self._state = speed != 0
        _LOGGER.debug(
            f"Updated fan {self.name} - "
            f"State: {'on' if self._state else 'off'}, "
            f"Percentage: {self.percentage}, Speed: {self._speed}, Status: {status}"
        )
        return previous != (self._state, self._speed)

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        return self._state

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage."""
        if not self._state:
            return 0
        return SPEED_TO_PERCENTAGE[self._speed]

    @property
    def speed_count(self) -> int:
        """Return the number of speeds the fan supports."""
        return int_states_in_range(SPEED_RANGE)

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs) -> None:
        """Turn the fan on."""
        _LOGGER.info("Turning on Smarteefi fan: %s percentage: %s", self.name, percentage)
        if percentage == 0:
            await self.async_turn_off()
            return
        if not await self._execute_cli("set-status", "1"):
            _LOGGER.warning(f"Failed to turn on fan {self.name}, syncing state to verify")
            await self._sync_state()
            return
        self._state = True
        if percentage:
            await self.async_set_percentage(percentage)
            return
        # If no percentage specified, keep the current speed or default to speed 2 (50%)
        if self._speed == 0:
            self._speed = 2
        self.async_write_ha_state()
        # Sync state after a short delay to verify actual device state
        await asyncio.sleep(0.5)
        await self._sync_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the fan off."""
        _LOGGER.info("Turning off Smarteefi fan: %s", self.name)
        if await self._execute_cli("set-status", "0"):
            self._state = False
            self._speed = 0
            self.async_write_ha_state()
            # Sync state after a short delay to verify actual device state
            await asyncio.sleep(0.5)
        else:
            _LOGGER.warning(f"Failed to turn off fan {self.name}, syncing state to verify")
        await self._sync_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan."""
        if percentage == 0:
            # Turn fan off if percentage is 0
            await self.async_turn_off()
            return

        speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        _LOGGER.info("Setting speed of Smarteefi fan: %s to %d", self.name, speed)
        if await self._execute_cli("set-speed", str(speed)):
            self._speed = speed
            self._state = True
            self.async_write_ha_state()
            # Sync state after a short delay to verify actual device state
            await asyncio.sleep(0.5)
        else:
            _LOGGER.warning(f"Failed to set fan speed for {self.name}, syncing state to verify")
        await self._sync_state()
