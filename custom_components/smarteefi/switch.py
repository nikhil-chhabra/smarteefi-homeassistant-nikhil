import logging
import asyncio
from homeassistant.components.switch import SwitchEntity
from .entity import SmarteefiEntity, build_entities

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Smarteefi switches from a config entry."""
    async_add_entities(build_entities(entry, "switch", SmarteefiSwitch))

class SmarteefiSwitch(SmarteefiEntity, SwitchEntity):
    """Representation of a Smarteefi switch."""

    def __init__(self, device, hacli_path, ip_address, netmask):
        super().__init__(device, hacli_path, ip_address, netmask, "Unnamed Switch")
        self._state = False

    def _apply_status(self, status):
        # ON if our smap bit is set in status
        new_state = bool(status & self._smap)
        if self._state == new_state:
            return False
        self._state = new_state
        _LOGGER.debug(
            f"Updated switch {self.name} - "
            f"State: {'on' if new_state else 'off'}, "
            f"Smap: {self._smap}, Status: {status}"
        )
        return True

    @property
    def is_on(self):
        """Return True if the switch is on."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        await self._set_state(True)

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        await self._set_state(False)

    async def _set_state(self, on):
        _LOGGER.info("Turning %s Smarteefi switch: %s", "on" if on else "off", self.name)
        if await self._execute_cli("set-status", "1" if on else "0"):
            # Optimistically update state
            self._state = on
            self.async_write_ha_state()
            # Sync state after a short delay to verify actual device state
            await asyncio.sleep(0.5)
        else:
            _LOGGER.warning(f"Failed to turn {'on' if on else 'off'} switch {self.name}, syncing state to verify")
        await self._sync_state()
