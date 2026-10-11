"""Config flow: sign in to VeSync and find the fryer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from pyvesync import VeSync
from pyvesync.utils.errors import VeSyncError, VeSyncLoginError

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ALLOW_UNVERIFIED, CONF_NOTIFY_SERVICE, DOMAIN
from .cosori358 import Cosori358, FryerError

_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)


class FryerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()
            manager = VeSync(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                session=async_get_clientsession(self.hass),
            )
            try:
                if not await manager.login():
                    errors["base"] = "invalid_auth"
                else:
                    fryer = await Cosori358.discover(manager)
                    return self.async_create_entry(title=fryer.name, data=user_input)
            except VeSyncLoginError:
                errors["base"] = "invalid_auth"
            except FryerError as err:
                errors["base"] = "no_fryer" if "No CS358" in str(err) else "cannot_connect"
            except VeSyncError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FryerOptionsFlow()


class FryerOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ALLOW_UNVERIFIED,
                    default=options.get(CONF_ALLOW_UNVERIFIED, False),
                ): bool,
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    description={"suggested_value": options.get(CONF_NOTIFY_SERVICE, "")},
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
