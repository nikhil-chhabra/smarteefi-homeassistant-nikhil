import asyncio
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from datetime import timedelta

import aiohttp
import psutil
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    HACLI_PATH,
    INITIAL_SYNC_INTERVAL,
    PLATFORMS,
    SYNC_INTERVAL,
    UDP_PORT,
)
from .entity import parse_device_id
from .hacli import run_hacli, set_executable_permissions

_LOGGER = logging.getLogger(__name__)

API_URL = "https://www.smarteefi.com/api/homeassistant_v1/user/devices"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


class SmarteefiApiError(Exception):
    """Raised when the Smarteefi cloud API cannot return the device list."""


@dataclass
class SmarteefiData:
    """Runtime data for a loaded config entry."""

    hacli_path: str
    coordinator: "SmarteefiDataUpdateCoordinator"
    udp_transport: asyncio.DatagramTransport | None = None


def _loaded_entries(hass: HomeAssistant):
    return [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Smarteefi integration."""
    _LOGGER.debug("Setting up Smarteefi integration")

    async def handle_refresh_devices(call: ServiceCall):
        """Handle the service call to refresh devices."""
        for entry in _loaded_entries(hass):
            await async_refresh_devices(hass, entry)

    async def handle_sync_states(call: ServiceCall):
        """Handle the service call to sync states."""
        for entry in _loaded_entries(hass):
            await entry.runtime_data.coordinator.async_sync_states()

    hass.services.async_register(DOMAIN, "discover_devices", handle_refresh_devices)
    hass.services.async_register(DOMAIN, "sync_states", handle_sync_states)
    return True

class SmarteefiDataUpdateCoordinator:
    """Class to manage fetching Smarteefi data with non-blocking startup."""

    def __init__(self, hass, entry, hacli_path):
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self._unsub_interval = None
        self._hacli_path = hacli_path
        self._is_initial_sync = True
        self._sync_lock = asyncio.Lock()
        self.ip_address = entry.data.get("ip_address")
        self.netmask = entry.data.get("netmask")

    def async_init(self):
        """Start periodic syncing without blocking startup."""
        self._is_initial_sync = True
        # Start with initial sync interval
        self._setup_interval(INITIAL_SYNC_INTERVAL)

    def _setup_interval(self, interval_seconds):
        """Setup or update the sync interval."""
        # Remove previous interval if exists
        if self._unsub_interval:
            self._unsub_interval()

        # Set up new interval
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._async_update,
            timedelta(seconds=interval_seconds)
        )
        _LOGGER.debug(f"Set sync interval to {interval_seconds} seconds")

    async def _async_update(self, now=None):

        if not self.hass.is_running:
            _LOGGER.debug("HA Not Yet Ready. Wait to load")
            return

        # If this was the initial sync, switch to regular interval
        if self._is_initial_sync:
            self._is_initial_sync = False
            self._setup_interval(SYNC_INTERVAL)

        # A slow sync must not stack up behind the interval timer
        if self._sync_lock.locked():
            _LOGGER.debug("Previous state sync still running, skipping this interval")
            return

        _LOGGER.debug("Performing periodic state sync for all Smarteefi devices")
        await self.async_sync_states()

    async def async_sync_states(self, entity_id=None):
        """Sync states for all devices or a specific entity."""
        devices = self.entry.data.get("devices", [])
        if not devices:
            _LOGGER.debug("No devices to sync")
            return

        async with self._sync_lock:
            if entity_id:
                # Sync only the specified entity
                entity_registry = er.async_get(self.hass)
                if entity_entry := entity_registry.async_get(entity_id):
                    device = next((d for d in devices if d["id"] == entity_entry.unique_id), None)
                    if device:
                        await self._sync_device_state(device, devices)
                return

            # Combine every channel on a board into one query: "serial:x" -> OR of smaps
            combined_devices = {}
            for device in devices:
                if parse_device_id(device.get("id", "")) is None:
                    _LOGGER.warning(f"Skipping device with invalid id: {device.get('id')}")
                    continue
                parts = device["id"].split(':')
                prefix = ':'.join(parts[:2])  # First two parts as key
                combined = combined_devices.setdefault(
                    prefix, {"smap": 0, "cloudid": device.get("cloudid", "")}
                )
                combined["smap"] |= int(parts[2])

            # Create the new list of devices with combined IDs
            new_devices = [
                {"id": f"{prefix}:{combined['smap']}", "cloudid": combined["cloudid"]}
                for prefix, combined in combined_devices.items()
            ]

            for index, device in enumerate(new_devices, 1):
                _LOGGER.debug(f"Processing device {index} of {len(new_devices)}")
                await self._sync_device_state(device, devices)
                if index < len(new_devices):
                    await asyncio.sleep(1)  # Brief pause between devices

    async def _sync_device_state(self, device, devices):
        """Sync state for a single device."""
        _LOGGER.debug(f"Syncing state for device {device['id']}")

        output = await run_hacli(
            self._hacli_path,
            [self.ip_address, self.netmask, "get-status", device["id"], str(device.get("cloudid", ""))]
        )
        if output is None:
            return

        try:
            status = int(output)
        except ValueError:
            _LOGGER.error(f"Invalid status output for {device['id']}: {output}")
            return

        prefix = ':'.join(device["id"].split(':')[:2])
        for dev in devices:
            parsed = parse_device_id(dev.get("id", ""))
            if parsed is None or not dev["id"].startswith(f"{prefix}:"):
                continue
            serial, smap = parsed
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_device_update_{serial}:{smap}",
                {"smap": smap, "status": status}
            )

    def async_unload(self):
        """Stop periodic syncing."""
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None


class SmarteefiUDPProtocol(asyncio.DatagramProtocol):
    """Receive status packets pushed by devices on the local network."""

    def __init__(self, hass: HomeAssistant, local_network):
        self.hass = hass
        self._local_network = local_network

    def datagram_received(self, data, addr):
        """Handle incoming UDP packets with custom binary format."""
        try:
            _LOGGER.debug(f"Received UDP packet from {addr}: {data.hex()}")

            # Packets are unauthenticated, so only accept them from our own subnet
            if ipaddress.ip_address(addr[0]) not in self._local_network:
                _LOGGER.debug(f"Ignoring UDP packet from outside local network: {addr[0]}")
                return

            # Verify minimum packet length (26 bytes)
            if len(data) < 26:
                _LOGGER.error(f"Packet too short: {len(data)} bytes")
                return

            # Parse serial number (first 16 bytes, null-terminated)
            serial = data[:16].split(b'\x00')[0].decode('ascii')

            # Verify separators
            if data[16] != ord(':') or data[21] != ord(':'):
                _LOGGER.error("Invalid packet format - missing separators")
                return

            # Parse smap (4 bytes little-endian)
            smap = int.from_bytes(data[17:21], byteorder='little', signed=False)

            # Parse status (4 bytes little-endian)
            status = int.from_bytes(data[22:26], byteorder='little', signed=False)

            _LOGGER.debug(f"Parsed packet - Serial: {serial}, Smap: {smap}, Status: {status}")

            # Signal the entity matching serial:smap to update
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_device_update_{serial}:{smap}",
                {"smap": smap, "status": status}
            )
        except Exception as e:
            _LOGGER.error(f"Error processing UDP packet: {e}")

    def error_received(self, exc):
        _LOGGER.debug(f"UDP server error: {exc}")


async def start_udp_server(hass: HomeAssistant, ip_address: str, netmask: str, port: int):
    """Start UDP server to listen for device updates."""
    local_network = ipaddress.ip_network(f"{ip_address}/{netmask}", strict=False)
    try:
        transport, _ = await hass.loop.create_datagram_endpoint(
            lambda: SmarteefiUDPProtocol(hass, local_network),
            local_addr=('0.0.0.0', port))
        _LOGGER.info(f"Started UDP server on port {port}")
        return transport
    except Exception as e:
        _LOGGER.error(f"Failed to start UDP server: {e}")
        return None

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Smarteefi integration from a config entry."""
    apitoken = entry.data.get("apitoken")
    if not apitoken:
        _LOGGER.error("API token is missing in config entry")
        return False

    if not await hass.async_add_executor_job(set_executable_permissions, HACLI_PATH):
        _LOGGER.error("Smarteefi CLI is not available for this platform")
        return False
    _LOGGER.debug(f"Using HACLI path: {HACLI_PATH}")

    # Auto-detect the active interface, IP address, and netmask
    network_interface, ip_address, netmask = await hass.async_add_executor_job(
        _get_active_interface_ip_and_netmask
    )
    if not network_interface or not ip_address or not netmask:
        raise ConfigEntryNotReady("Unable to determine active network interface, IP address, or netmask")

    _LOGGER.debug(f"Detected active interface: {network_interface}, IP: {ip_address}, Netmask: {netmask}")

    # Update the config entry with the latest interface details
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "network_interface": network_interface, "ip_address": ip_address, "netmask": netmask}
    )

    # Fetch devices if none are stored yet (also retries an earlier failed fetch)
    if not entry.data.get("devices"):
        session = async_get_clientsession(hass)
        try:
            devices = await fetch_devices(session, apitoken)
        except SmarteefiApiError as e:
            raise ConfigEntryNotReady(f"Error fetching Smarteefi devices: {e}") from e

        hass.config_entries.async_update_entry(entry, data={**entry.data, "devices": devices})
        _LOGGER.info("Smarteefi devices discovered: %s", devices)

    coordinator = SmarteefiDataUpdateCoordinator(hass, entry, HACLI_PATH)
    entry.runtime_data = SmarteefiData(hacli_path=HACLI_PATH, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start UDP server
    udp_transport = await start_udp_server(hass, ip_address, netmask, UDP_PORT)
    if udp_transport is None:
        _LOGGER.warning("UDP server failed to start, continuing without real-time updates")
    entry.runtime_data.udp_transport = udp_transport

    coordinator.async_init()

    # Reload when the token (options flow) or device list (discover_devices) changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True

async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Reload the entry after its data changed."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data: SmarteefiData = entry.runtime_data
    data.coordinator.async_unload()

    if data.udp_transport:
        data.udp_transport.close()
        data.udp_transport = None
        _LOGGER.info("Stopped UDP server")

    # Unload all platforms
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

def _get_active_interface_ip_and_netmask():
    """Determine the active interface with the default gateway and return its name, IP, and netmask.

    Blocking: run in an executor.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]

        interfaces = psutil.net_if_addrs()
        for iface, addrs in interfaces.items():
            if iface in ['lo', 'lo0']:
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address == local_ip:
                    return iface, addr.address, addr.netmask

        _LOGGER.warning("No active interface found matching the default gateway IP.")
        return None, None, None
    except Exception as e:
        _LOGGER.error(f"Error determining active interface: {e}")
        return None, None, None

async def fetch_devices(session: aiohttp.ClientSession, apitoken: str):
    """Fetch devices from Smarteefi API using POST request. Raises SmarteefiApiError on failure."""
    payload = {"UserDevice": {"hatoken": apitoken}}
    headers = {"Content-Type": "application/json"}

    try:
        async with session.post(
            API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            response_text = await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise SmarteefiApiError(f"request failed: {e}") from e

    if response.status != 200:
        raise SmarteefiApiError(f"status {response.status}, response: {response_text}")

    try:
        json_response = json.loads(response_text)
    except ValueError as e:
        raise SmarteefiApiError(f"invalid JSON response: {response_text}") from e

    if json_response.get("result") != "success":
        raise SmarteefiApiError(f"API returned failure: {json_response}")

    return json_response.get("devices", [])

async def async_refresh_devices(hass: HomeAssistant, entry: ConfigEntry):
    """Refresh devices from Smarteefi API."""
    session = async_get_clientsession(hass)
    apitoken = entry.data.get("apitoken")

    if not apitoken:
        _LOGGER.error("API token is missing in config entry")
        return False

    try:
        devices = await fetch_devices(session, apitoken)
    except SmarteefiApiError as e:
        # Keep the current devices rather than treating a failed fetch as "no devices"
        _LOGGER.error("Error refreshing Smarteefi devices: %s", e)
        return False

    _LOGGER.debug("Devices refreshed successfully: %s", devices)

    # Remove entities for devices that are no longer present
    new_device_ids = {device['id'] for device in devices}
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity_entry.unique_id not in new_device_ids:
            entity_registry.async_remove(entity_entry.entity_id)

    _LOGGER.info("Smarteefi devices refreshed: %s", devices)

    # Saving a changed device list triggers the update listener, which reloads the platforms
    hass.config_entries.async_update_entry(entry, data={**entry.data, "devices": devices})
    return True
