"""Sign in to VeSync and find the fryer; sign in again when the password changes."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthError, FryerClient, FryerError, NoFryer, RateLimited, TokenRejected, TooManyFryers
from .const import (
    CONF_ACCOUNT_ID,
    CONF_COUNTRY_CODE,
    CONF_LAST_LOGIN,
    CONF_REGION,
    CONF_TERMINAL_ID,
    CONF_TOKEN,
    DOMAIN,
    RELOGIN_COOLDOWN_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})
PASSWORD_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class FryerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        # Kept between attempts, so retrying after "no fryer" doesn't sign in again.
        self._client: FryerClient | None = None
        self._credentials: tuple[str, str] | None = None
        self._login_time: float | None = None

    async def _sign_in(
        self, email: str, password: str, terminal_id: str
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """Return (entry data, fryer name, error key)."""
        if self._client is None or self._credentials != (email, password) or self._client.session is None:
            self._client = FryerClient(
                async_get_clientsession(self.hass),
                email,
                password,
                terminal_id,
                time_zone=self.hass.config.time_zone,
            )
            self._credentials = (email, password)
        client = self._client
        try:
            if client.session is None:
                if self._login_time is not None and time.time() - self._login_time < RELOGIN_COOLDOWN_SECONDS:
                    return None, None, "rate_limited"  # Repeated submits must not hammer VeSync.
                self._login_time = time.time()
                await client.login()
            device = await client.find_fryer()
        except AuthError:
            self._client = None
            return None, None, "invalid_auth"
        except RateLimited:
            return None, None, "rate_limited"
        except TokenRejected:
            client.session = None  # Sign in again next time, once the cooldown allows.
            return None, None, "cannot_connect"
        except NoFryer:
            return None, None, "no_fryer"
        except TooManyFryers:
            return None, None, "multiple_fryers"
        except FryerError:
            return None, None, "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error signing in to VeSync")
            return None, None, "unknown"
        session = client.session
        if session is None:
            return None, None, "unknown"
        data = {
            CONF_EMAIL: email,
            CONF_PASSWORD: password,
            CONF_TERMINAL_ID: client.terminal_id,
            CONF_TOKEN: session.token,
            CONF_ACCOUNT_ID: session.account_id,
            CONF_COUNTRY_CODE: session.country_code,
            CONF_REGION: session.region,
            # Starts the re-login cooldown from this sign-in.
            CONF_LAST_LOGIN: self._login_time,
        }
        return data, device.name, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            # Checked before signing in: a login would sign the existing entry out.
            if any(
                entry.data[CONF_EMAIL].lower() == email.lower() for entry in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")
            # Holds the account for this form, so a second form for it is refused rather
            # than signing in alongside and knocking this one out.
            await self.async_set_unique_id(f"email:{email.lower()}")
            terminal_id = self._client.terminal_id if self._client else "2" + uuid.uuid4().hex
            data, name, error = await self._sign_in(email, user_input[CONF_PASSWORD], terminal_id)
            if data is None:
                errors["base"] = error or "unknown"
            else:
                await self.async_set_unique_id(data[CONF_ACCOUNT_ID], raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=name or "Air fryer", data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, _, error = await self._sign_in(
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD], entry.data[CONF_TERMINAL_ID]
            )
            if data is None:
                errors["base"] = error or "unknown"
            else:
                account_id = data[CONF_ACCOUNT_ID]
                if entry.unique_id and entry.unique_id.lower() == entry.data[CONF_EMAIL].lower():
                    # Set up before 0.3.0, when the email was the unique id.
                    if self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, account_id):
                        return self.async_abort(reason="already_configured")
                    self.hass.config_entries.async_update_entry(entry, unique_id=account_id)
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
