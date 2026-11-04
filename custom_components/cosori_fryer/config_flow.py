"""User initiated login and reauthentication."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from custom_components.cosori_fryer._vendor.pyvesync.utils.errors import VeSyncError

from . import create_manager
from .const import DOMAIN
from .coordinator import AUTH_ERRORS

USER_SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})


class FryerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Store a session only after an explicit form submission."""

    VERSION = 3

    def __init__(self) -> None:
        self._manager = None
        self._credentials = None

    async def _validate(self, credentials):
        if self._credentials != credentials:
            self._manager = create_manager(self.hass, credentials)
            self._credentials = credentials.copy()
        manager = self._manager
        try:
            async with asyncio.timeout(30):
                if not manager.auth.is_authenticated and not await manager.login():
                    return None, "invalid_auth"
                if not await manager.get_devices():
                    return None, "cannot_connect"
        except AUTH_ERRORS:
            return None, "invalid_auth"
        except VeSyncError, ClientError, TimeoutError:
            return None, "cannot_connect"
        devices = [device for device in manager.devices if device.device_type == "CS358-AF"]
        if len(devices) != 1:
            return None, "no_fryer" if not devices else "multiple_fryers"
        return {
            **credentials,
            "token": manager.token,
            "account_id": manager.account_id,
            "country_code": manager.country_code,
            "region": manager.current_region,
        }, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            user_input = {**user_input, CONF_EMAIL: user_input[CONF_EMAIL].strip().lower()}
            if any(
                entry.data[CONF_EMAIL].lower() == user_input[CONF_EMAIL]
                for entry in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(f"email:{user_input[CONF_EMAIL]}")
            data, error = await self._validate(user_input)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(data["account_id"], raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Cosori air fryer", data=data)
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            data, error = await self._validate({CONF_EMAIL: entry.data[CONF_EMAIL], **user_input})
            if error:
                errors["base"] = error
            else:
                # Legacy entries can have an email ID or no ID. The form always
                # signs in with the stored email, so those can acquire an account ID.
                legacy_ids = (None, entry.data[CONF_EMAIL].lower(), f"email:{entry.data[CONF_EMAIL].lower()}")
                check_id = data["account_id"]
                if entry.unique_id in legacy_ids:
                    if entry.data.get("account_id", data["account_id"]) != data["account_id"]:
                        return self.async_abort(reason="wrong_account")
                    check_id = entry.unique_id
                await self.async_set_unique_id(check_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data=data, unique_id=data["account_id"])
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
