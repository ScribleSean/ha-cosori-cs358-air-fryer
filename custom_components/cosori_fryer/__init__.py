"""Cosori CS358-AF through pyvesync."""

from __future__ import annotations

import asyncio
import logging
import math

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from custom_components.cosori_fryer._vendor.pyvesync.utils.errors import VeSyncError

from . import planner
from .const import DOMAIN
from .coordinator import AUTH_ERRORS, FryerConfigEntry, FryerCoordinator, make_plan
from .manager import FryerManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def finite_number(value):
    """Reject booleans and non-finite values instead of silently coercing them."""
    if isinstance(value, bool):
        raise vol.Invalid("Expected a number")
    try:
        value = float(value)
    except (ValueError, TypeError) as err:
        raise vol.Invalid("Expected a number") from err
    if not math.isfinite(value):
        raise vol.Invalid("Expected a finite number")
    return value


def loaded_coordinator(hass: HomeAssistant, call: ServiceCall) -> FryerCoordinator:
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id := call.data.get("config_entry_id"):
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    if len(entries) != 1 or entries[0].state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="choose_account")
    return entries[0].runtime_data


COOK_SCHEMA = vol.Schema(
    {
        vol.Required("temperature"): finite_number,
        vol.Required("minutes"): finite_number,
        vol.Optional("preheat", default=False): vol.Boolean(),
        vol.Optional("temperature_unit"): vol.In(["°F", "°C"]),
        vol.Optional("config_entry_id"): cv.string,
    }
)
KEEP_WARM_SCHEMA = vol.Schema(
    {
        vol.Optional("minutes"): finite_number,
        vol.Optional("config_entry_id"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the cook and keep warm actions."""

    async def cook(call: ServiceCall) -> None:
        coordinator = loaded_coordinator(hass, call)
        temperature = call.data["temperature"]
        if call.data.get("temperature_unit", hass.config.units.temperature_unit) == "°C":
            temperature = f"{temperature:g}C"
        plan = make_plan(
            planner.Intent(
                "cook", temp_f=temperature, minutes=call.data["minutes"], preheat=call.data["preheat"]
            )
        )
        await coordinator.cook(plan.temp_f, plan.minutes, plan.preheat)
        _LOGGER.info("Started cook at %s °F for %s minutes", plan.temp_f, plan.minutes)

    async def keep_warm(call: ServiceCall) -> None:
        await loaded_coordinator(hass, call).keep_warm(call.data.get("minutes"))

    # start_cook is the 0.7 name, kept so existing automations keep working.
    for name in ("cook", "start_cook"):
        hass.services.async_register(DOMAIN, name, cook, schema=COOK_SCHEMA)
    hass.services.async_register(DOMAIN, "keep_warm", keep_warm, schema=KEEP_WARM_SCHEMA)
    return True


def create_manager(hass, data) -> FryerManager:
    """Use HA's shared HTTP session, without creating files or another session."""
    return FryerManager(
        data[CONF_EMAIL],
        data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
        time_zone=hass.config.time_zone,
    )


async def async_setup_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    manager = create_manager(hass, entry.data)
    if not entry.data.get("token"):
        raise ConfigEntryAuthFailed
    manager.set_credentials(
        entry.data["token"],
        entry.data["account_id"],
        entry.data.get("country_code", "US"),
        entry.data.get("region", "US"),
    )
    try:
        async with asyncio.timeout(15):
            if not await manager.get_devices():
                raise ConfigEntryAuthFailed
    except AUTH_ERRORS as err:
        raise ConfigEntryAuthFailed from err
    except (VeSyncError, ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady from err
    devices = [device for device in manager.devices if device.device_type == "CS358-AF"]
    if len(devices) != 1:
        raise ConfigEntryNotReady("Exactly one CS358-AF must be shared with this account")
    coordinator = FryerCoordinator(hass, entry, manager, devices[0])
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    """Preserve existing sessions and remove controls retired in 0.5."""
    if entry.version > 3:
        return False
    if entry.version < 3:
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                entity.domain == "number"
                or (entity.domain == "button" and entity.unique_id.endswith("_preheat"))
                or (
                    entity.domain == "sensor"
                    and any(
                        entity.unique_id.endswith(f"_{key}")
                        for key in ("cook_set_time", "preheat_set_time", "remaining_time", "time_remaining")
                    )
                )
            ):
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(
            entry,
            unique_id=entry.data.get("account_id") or entry.data[CONF_EMAIL].strip().lower(),
            version=3,
        )
    return True
