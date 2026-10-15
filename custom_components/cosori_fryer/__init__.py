"""COSORI CS358-AF air fryer, controlled through the VeSync cloud."""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import (
    MINUTES_MAX,
    MINUTES_MIN,
    TEMP_MAX,
    TEMP_MIN,
    TEMP_STEP,
    FryerClient,
    Session,
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


def _whole(value: Any) -> int:
    """Accept whole numbers only, so 170.9 or 1.9 is refused instead of silently cut down."""
    if isinstance(value, bool):
        raise vol.Invalid("Must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("Must be a number") from err
    if not number.is_integer():
        raise vol.Invalid("Must be a whole number")
    return int(number)


TEMPERATURE = vol.All(_whole, vol.Range(min=TEMP_MIN, max=TEMP_MAX), _in_steps)
MINUTES = vol.All(_whole, vol.Range(min=MINUTES_MIN, max=MINUTES_MAX))


def _coordinator(hass: HomeAssistant, entry_id: str | None = None) -> FryerCoordinator:
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id is None:
        # Include unloaded entries: a disconnected fryer must not silently redirect a cook.
        if len(entries) > 1:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="choose_account")
        entry = entries[0] if entries else None
    else:
        entry = next((entry for entry in entries if entry.entry_id == entry_id), None)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return entry.runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the fryer's action."""

    async def start_cooking(call: ServiceCall) -> None:
        await _coordinator(hass, call.data.get("config_entry_id")).start_cooking(
            call.data["temperature"],
            call.data["cooking_time"],
            preheat=call.data["preheat"],
        )

    hass.services.async_register(
        DOMAIN,
        "start_cooking",
        start_cooking,
        vol.Schema(
            {
                vol.Required("temperature"): TEMPERATURE,
                vol.Required("cooking_time"): MINUTES,
                vol.Optional("preheat", default=False): cv.boolean,
                vol.Optional("config_entry_id"): cv.string,
            }
        ),
    )
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
    if not _first_for_account(hass, entry):
        # Possible with setups from before 0.3.0. Stopping before any request avoids
        # a login that would sign the other entry out.
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="duplicate_account")
    coordinator = FryerCoordinator(hass, entry, client)
    # Finds the fryer, then reads it. The stored session is reused; a new
    # login only happens if VeSync rejects it.
    await coordinator.async_config_entry_first_refresh()
    if not _first_for_account(hass, entry):  # The account id may only be known now.
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="duplicate_account")
    if client.device is not None:  # Always set once the first refresh succeeded.
        await _migrate_unique_ids(hass, entry, client.device.cid)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _first_for_account(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    """Whether this entry may load: no other entry for its account is loaded, and
    otherwise the oldest wins. Decided without a request, so entries starting at
    the same time agree.
    """
    email = entry.data[CONF_EMAIL].lower()
    account_ids = {entry.data.get(CONF_ACCOUNT_ID), entry.unique_id} - {None}
    same = [
        other
        for other in hass.config_entries.async_entries(DOMAIN, include_ignore=False, include_disabled=False)
        if other.data.get(CONF_EMAIL, "").lower() == email
        or account_ids & {other.data.get(CONF_ACCOUNT_ID), other.unique_id}
    ]
    if any(other is not entry and other.state is ConfigEntryState.LOADED for other in same):
        return False
    return not same or min(same, key=lambda other: (other.created_at, other.entry_id)) is entry


# Entity keys used by earlier releases, by platform, and what they are now.
_RENAMED = {
    "sensor": {
        "status": "cook_status",
        "target_temp": "cook_set_temp",
        "set_temperature": "cook_set_temp",
        "time_remaining": "remaining_time",
    },
    "number": {
        "target_temp": "cook_temperature",
        "temperature": "cook_temperature",
        "cook_minutes": "cook_time",
        "minutes": "cook_time",
    },
    "button": {"air_fry": "start"},
}


async def _migrate_unique_ids(hass: HomeAssistant, entry: FryerConfigEntry, cid: str) -> None:
    """Keep the device, entities, their IDs and history across the renames in 0.3 and 0.4."""
    devices = dr.async_get(hass)
    old_device = devices.async_get_device({(DOMAIN, entry.entry_id)})
    if old_device and not devices.async_get_device({(DOMAIN, cid)}):
        # 0.2 identified the device by entry id; keep the same device, and its device_id.
        devices.async_update_device(old_device.id, new_identifiers={(DOMAIN, cid)})

    registry = er.async_get(hass)

    @callback
    def rename(entity: er.RegistryEntry) -> dict[str, str] | None:
        for prefix in (f"{entry.entry_id}_", f"{cid}_"):
            if not entity.unique_id.startswith(prefix):
                continue
            old_key = entity.unique_id.removeprefix(prefix)
            new_key = _RENAMED.get(entity.domain, {}).get(old_key, old_key)
            new_id = f"{cid}_{new_key}"
            if new_id == entity.unique_id or registry.async_get_entity_id(entity.domain, DOMAIN, new_id):
                return None
            return {"new_unique_id": new_id}
        return None

    await er.async_migrate_entries(hass, entry.entry_id, rename)


async def async_migrate_entry(hass: HomeAssistant, entry: FryerConfigEntry) -> bool:
    """Version 1 entries signed in on every start and had no client id."""
    if entry.version > 2:
        return False  # Written by a newer release.
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
