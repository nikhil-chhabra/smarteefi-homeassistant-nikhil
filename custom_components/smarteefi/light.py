import logging

from homeassistant.components.light import LightEntity, ColorMode, ATTR_BRIGHTNESS, ATTR_RGB_COLOR
from .entity import SmarteefiEntity, build_entities

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Smarteefi lights from a config entry."""
    async_add_entities(build_entities(entry, "light", SmarteefiLight))

class SmarteefiLight(SmarteefiEntity, LightEntity):
    """Representation of a Smarteefi Light."""

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, device, hacli_path, ip_address, netmask):
        super().__init__(device, hacli_path, ip_address, netmask, "Unnamed Light")
        self._state = False
        self._brightness = 255  # Full brightness by default
        self._rgb_color = (255, 255, 255)  # Default white color

    def _apply_status(self, status):
        previous = (self._state, self._brightness, self._rgb_color)

        # Status packs the colour as 0xRRGGBB00
        r = (status & 0xFF000000) >> 24
        g = (status & 0x00FF0000) >> 16
        b = (status & 0x0000FF00) >> 8
        if r or g or b:
            self._state = True
            self._rgb_color = (r, g, b)
            self._brightness = max(r, g, b)
        else:
            # Keep the last colour and brightness so a plain "turn on" restores them
            self._state = False

        _LOGGER.debug(
            f"Updated light {self.name} - "
            f"State: {'on' if self._state else 'off'}, "
            f"Brightness: {self._brightness}, Color: {self._rgb_color}"
        )
        return previous != (self._state, self._brightness, self._rgb_color)

    @property
    def is_on(self):
        """Return True if the light is on."""
        return self._state

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def rgb_color(self):
        """Return the RGB color of the light."""
        return self._rgb_color

    async def async_turn_on(self, **kwargs):
        """Turn the light on with optional brightness and color control."""
        _LOGGER.info(f"Turning on Smarteefi light: {self.name}")

        brightness = kwargs.get(ATTR_BRIGHTNESS, self._brightness)
        rgb_color = tuple(kwargs.get(ATTR_RGB_COLOR, self._rgb_color))
        if not any(rgb_color):
            rgb_color = (255, 255, 255)
        intensity = self.convert_to_100_range(brightness)

        if not intensity:
            await self.async_turn_off()
            return

        if await self._execute_cli("set-rgb-color", self.rgb_to_hex(rgb_color)):
            self._state = True
            self._rgb_color = rgb_color
            self.async_write_ha_state()

        if await self._execute_cli("set-intensity", str(intensity)):
            self._brightness = brightness
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the light off."""
        _LOGGER.info(f"Turning off Smarteefi light: {self.name}")
        if await self._execute_cli("set-status", "0"):
            self._state = False
            self.async_write_ha_state()

    @staticmethod
    def rgb_to_hex(rgb_color):
        r, g, b = [max(0, min(255, int(x))) for x in rgb_color]
        return "#{:02X}{:02X}{:02X}".format(r, g, b)

    @staticmethod
    def convert_to_100_range(value):
        value = max(0, min(255, value))
        if value == 0:
            return 0
        return max(1, round((value / 255) * 100))  # Never round a lit brightness down to off
