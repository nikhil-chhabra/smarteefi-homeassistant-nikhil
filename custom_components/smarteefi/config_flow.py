import asyncio
import logging
import os
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, HACLI_PATH

_LOGGER = logging.getLogger(__name__)

API_URL = "https://www.smarteefi.com/api/homeassistant_v1/user/validatehatoken"


async def _validate_api_token(hass, apitoken):
    """Validate the API token against the cloud API. Return an error key, or None if valid."""
    payload = {
        "UserDevice": {
            "hatoken": apitoken
        }
    }
    headers = {
        "Content-Type": "application/json",
    }

    try:
        session = async_get_clientsession(hass)
        async with session.post(
            API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if response.status != 200:
                _LOGGER.error(f"API request failed with status code: {response.status}")
                return "cannot_connect"
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        _LOGGER.error(f"Exception occurred while validating API token: {e}")
        return "cannot_connect"

    if data.get("result") != "success":
        _LOGGER.error(f"API token validation failed: {data.get('error_desc', data)}")
        return "invalid_token"
    return None


class SmarteefiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smarteefi IoT Platform."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Prompt user to enter apitoken."""
        # One entry per Home Assistant: the UDP listener and device polling are host-wide
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if not await self.hass.async_add_executor_job(os.path.exists, HACLI_PATH):
            _LOGGER.error(f"CLI not found at {HACLI_PATH}")
            return self.async_abort(reason="unsupported_platform")

        errors = {}

        if user_input is not None:
            apitoken = user_input["apitoken"]

            # Validate the API token
            error = await _validate_api_token(self.hass, apitoken)
            if error:
                errors["base"] = error
            else:
                # Network details are detected when the entry is set up
                return self.async_create_entry(
                    title=DOMAIN,
                    data={
                        "apitoken": apitoken,
                        "network_interface": "",
                        "ip_address": "",
                        "netmask": "",
                    }
                )

        # Show form for entering apitoken
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("apitoken"): str,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return SmarteefiOptionsFlowHandler()


class SmarteefiOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for editing apitoken."""

    async def async_step_init(self, user_input=None):
        """Allow user to update apitoken."""
        errors = {}

        if user_input is not None:
            # Validate the API token
            error = await _validate_api_token(self.hass, user_input["apitoken"])
            if error:
                errors["base"] = error
            else:
                # The token lives in entry.data, which the integration reads; the
                # entry's update listener reloads it with the new token.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, "apitoken": user_input["apitoken"]},
                )
                return self.async_create_entry(title="", data={})

        # Show form for updating apitoken
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("apitoken", default=self.config_entry.data["apitoken"]): str,
            }),
            errors=errors,
        )
