"""COSORI CS358-AF air fryer, controlled through the VeSync cloud."""

from __future__ import annotations

import uuid

import voluptuous as vol
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

from .api import (
    MINUTES_MAX,
    MINUTES_MIN,
    TEMP_MAX,
    TEMP_MIN,
    TEMP_STEP,
    AuthError,
    FryerClient,
    FryerError,
    Session,
    TokenRejected,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_COUNTRY_CODE,
    CONF_REGION,
    CONF_TERMINAL_ID,
    CONF_TOKEN,
    DOMAIN,
)
from .coordinator import FryerConfigEntry, FryerCoordinator

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _in_steps(value: int) -> int:
    if value % TEMP_STEP:
        raise vol.Invalid(f"Temperature must be a multiple of {TEMP_STEP}")
    return value


TEMPERATURE = vol.All(vol.Coerce(int), vol.Range(min=TEMP_MIN, max=TEMP_MAX), _in_steps)
MINUTES = vol.All(vol.Coerce(int), vol.Range(min=MINUTES_MIN, max=MINUTES_MAX))


def _coordinator(hass: HomeAssistant) -> FryerCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_loaded"
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the fryer's actions."""

    async def preheat(call: ServiceCall) -> None:
        await _coordinator(hass).preheat(call.data["temperature"])

    async def air_fry(call: ServiceCall) -> None:
        await _coordinator(hass).air_fry(call.data["temperature"], call.data["minutes"])

    async def stop(call: ServiceCall) -> None:
        await _coordinator(hass).stop()

    hass.services.async_register(
        DOMAIN,
        "preheat",
        preheat,
        vol.Schema({vol.Required("temperature"): TEMPERATURE}),
    )
    hass.services.async_register(
        DOMAIN,
        "air_fry",
        air_fry,
        vol.Schema(
            {vol.Required("temperature"): TEMPERATURE, vol.Required("minutes"): MINUTES}
        ),
    )
    hass.services.async_register(DOMAIN, "stop", stop)
    return True


def stored_session(entry: FryerConfigEntry) -> Session | None:
    if not entry.data.get(CONF_TOKEN):
        return None
    return Session(
        token=entry.data[CONF_TOKEN],
        account_id=entry.data[CONF_ACCOUNT_ID],
        country_code=entry.data.get(CONF_COUNTRY_CODE, "US"),
        region=entry.data.get(CONF_REGION, "US"),
    )


async def async_setup_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    client = FryerClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_TERMINAL_ID],
        stored_session(entry),
        hass.config.time_zone,
    )
    coordinator = FryerCoordinator(hass, entry, client)
    try:
        # Reuse the stored session; a new login only happens if VeSync rejects it.
        await coordinator._signed_in(client.find_fryer)  # noqa: SLF001
    except (AuthError, ConfigEntryAuthFailed) as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (FryerError, TokenRejected) as err:
        raise ConfigEntryNotReady(str(err)) from err

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    """Version 1 entries signed in on every start and had no client id."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_TERMINAL_ID: "2" + uuid.uuid4().hex},
            options={},
            version=2,
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
