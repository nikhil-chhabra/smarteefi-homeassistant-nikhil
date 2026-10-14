import logging

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from .entity import SmarteefiEntity, build_entities

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Smarteefi covers from a config entry."""
    async_add_entities(build_entities(entry, "cover", SmarteefiCover))

class SmarteefiCover(SmarteefiEntity, CoverEntity):
    """Representation of a Smarteefi cover."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, device, hacli_path, ip_address, netmask):
        super().__init__(device, hacli_path, ip_address, netmask, "Unnamed Cover")
        self._current_position = 0  # Assume the cover is fully closed initially

    def _apply_status(self, status):
        # Status may be the combined bitmap of every channel on the board, so test our bit only
        new_position = 100 if status & self._smap else 0
        _LOGGER.debug(
            f"Updated cover {self.name} - "
            f"Current Position: {new_position}, Status: {status}"
        )
        if self._current_position == new_position:
            return False
        self._current_position = new_position
        return True

    @property
    def is_closed(self):
        """Return True if the cover is closed."""
        return self._current_position == 0

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self._current_position

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        _LOGGER.info("Opening Smarteefi cover: %s", self.name)
        if await self._execute_cli("set-status", "1"):
            self._current_position = 100
            self.async_write_ha_state()

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        _LOGGER.info("Closing Smarteefi cover: %s", self.name)
        if await self._execute_cli("set-status", "0"):
            self._current_position = 0
            self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION, 0)
        _LOGGER.info(f"Setting Smarteefi cover position to {position}: {self.name}")

        prev_position = self._current_position

        # Determine the direction and how far to travel
        if position == prev_position:
            return
        if position == 0:
            status, percentage = "0", "0"
        elif position == 100:
            status, percentage = "1", "0"
        elif position > prev_position:
            status, percentage = "1", str(position - prev_position)
        else:
            status, percentage = "0", str(prev_position - position)

        if await self._execute_cli("set-status", status, percentage):
            self._current_position = position
            self.async_write_ha_state()
