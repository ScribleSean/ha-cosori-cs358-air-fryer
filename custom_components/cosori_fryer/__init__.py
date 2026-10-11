"""COSORI CS358-AF air fryer, controlled through the VeSync cloud."""

from __future__ import annotations

import voluptuous as vol
from pyvesync import VeSync
from pyvesync.utils.errors import VeSyncError, VeSyncLoginError

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import FryerConfigEntry, FryerCoordinator
from .cosori358 import MAX_MINUTES, MIN_MINUTES, Cosori358, FryerError

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_COOK_SCHEMA = vol.Schema(
    {
        vol.Required("temp"): vol.Coerce(int),
        vol.Required("minutes"): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_MINUTES, max=MAX_MINUTES)
        ),
    }
)
_SERVICES = {
    "preheat": _COOK_SCHEMA,
    "cook": _COOK_SCHEMA,
    "start": None,
    "stop": None,
}


def _coordinator(hass: HomeAssistant) -> FryerCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError("The air fryer integration is not set up.")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the fryer services."""

    async def handle(call: ServiceCall) -> None:
        await _coordinator(hass).async_command(
            call.service, call.data.get("temp"), call.data.get("minutes")
        )

    for name, schema in _SERVICES.items():
        hass.services.async_register(DOMAIN, name, handle, schema=schema)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    manager = VeSync(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
        time_zone=hass.config.time_zone,
    )
    try:
        if not await manager.login():
            raise ConfigEntryAuthFailed("VeSync login failed.")
        fryer = await Cosori358.discover(manager)
    except VeSyncLoginError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (VeSyncError, FryerError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = FryerCoordinator(hass, entry, manager, fryer)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
