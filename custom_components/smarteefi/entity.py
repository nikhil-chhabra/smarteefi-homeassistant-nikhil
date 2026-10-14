import logging

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .hacli import run_hacli

_LOGGER = logging.getLogger(__name__)


def parse_device_id(device_id):
    """Split a device id ("serial:ignored:smap") into (serial, smap), or None if malformed."""
    parts = str(device_id).split(':')
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[2])
    except ValueError:
        return None


def build_entities(entry, device_type, entity_cls):
    """Create entities of one device type from the config entry."""
    data = entry.runtime_data
    devices = entry.data.get("devices", [])
    ip_address = entry.data.get("ip_address")
    netmask = entry.data.get("netmask")

    if not ip_address or not netmask:
        _LOGGER.error(f"Missing network configuration for Smarteefi {device_type}.")
        return []

    entities = []
    for device in devices:
        if device.get("type") != device_type:
            continue
        if parse_device_id(device.get("id", "")) is None:
            _LOGGER.error(f"Invalid unique_id format: {device.get('id')}")
            continue
        entities.append(entity_cls(device, data.hacli_path, ip_address, netmask))
    return entities


class SmarteefiEntity(Entity):
    """Common behaviour for all Smarteefi entities."""

    _attr_should_poll = False

    def __init__(self, device, hacli_path, ip_address, netmask, default_name):
        self._device = device
        self._attr_name = device.get("name", default_name)
        self._attr_unique_id = device["id"]  # Format: "serial:ignored:smap"
        self._cloud_id = device.get("cloudid", "")
        self._hacli = hacli_path
        self.ip_address = ip_address
        self.netmask = netmask

        serial, self._smap = parse_device_id(device["id"])
        self._entity_match_id = f"{serial}:{self._smap}"  # serial:smap

    async def async_added_to_hass(self):
        """Register update dispatcher."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_device_update_{self._entity_match_id}",
                self._handle_device_update
            )
        )

    @callback
    def _handle_device_update(self, data):
        """Update state from a UDP message or a status poll."""
        # Only process if smap matches our entity's smap
        if data["smap"] != self._smap:
            return
        if self._apply_status(data["status"]):
            self.async_write_ha_state()

    def _apply_status(self, status) -> bool:
        """Update internal state from a device status. Return True if anything changed."""
        raise NotImplementedError

    async def _execute_cli(self, command, *args):
        """Run a HACLI command against this device. Return True on success."""
        output = await run_hacli(
            self._hacli,
            [self.ip_address, self.netmask, command, self.unique_id, str(self._cloud_id), *args]
        )
        return output is not None

    async def _sync_state(self):
        """Fetch the current status from the device and apply it."""
        output = await run_hacli(
            self._hacli,
            [self.ip_address, self.netmask, "get-status", self.unique_id, str(self._cloud_id)]
        )
        if output is None:
            return
        try:
            status = int(output)
        except ValueError:
            _LOGGER.error(f"Invalid status output for {self.name}: {output}")
            return
        self._handle_device_update({"smap": self._smap, "status": status})
