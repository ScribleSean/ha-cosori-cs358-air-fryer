"""Polling, session handling and commands for the fryer."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, FryerClient, FryerError, FryerStatus, TokenRejected
from .const import (
    ACTIVE_LINGER_SECONDS,
    CONF_ACCOUNT_ID,
    CONF_COUNTRY_CODE,
    CONF_REGION,
    CONF_TOKEN,
    DEFAULT_MINUTES,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    EVENT_COOK_DONE,
    EVENT_PREHEAT_DONE,
    POLL_ACTIVE,
    POLL_IDLE,
    RELOGIN_COOLDOWN_SECONDS,
    STATUS,
)

_LOGGER = logging.getLogger(__name__)

# Statuses worth watching closely.
_BUSY = {
    "heating",
    "preheatStop",
    "preheatEnd",
    "ready",
    "cooking",
    "cookStop",
    "keeping",
}

type FryerConfigEntry = ConfigEntry[FryerCoordinator]


class FryerCoordinator(DataUpdateCoordinator[FryerStatus]):
    """Reads the fryer every 30 s while busy and every minute when idle."""

    config_entry: FryerConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: FryerConfigEntry, client: FryerClient
    ) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_IDLE
        )
        self.client = client
        # The values the Preheat and Air fry buttons use, set by the number entities.
        self.temperature = DEFAULT_TEMPERATURE
        self.minutes = DEFAULT_MINUTES
        self._busy_until = 0.0
        self._last_login = 0.0

    async def _signed_in[T](self, request: Callable[[], Awaitable[T]]) -> T:
        """Run a request, signing in again once if VeSync rejects the token."""
        try:
            return await request()
        except TokenRejected:
            if time.monotonic() - self._last_login < RELOGIN_COOLDOWN_SECONDS:
                raise FryerError(
                    "Signed out by another VeSync login; trying again shortly"
                ) from None
            self._last_login = time.monotonic()
            try:
                session = await self.client.login()
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_TOKEN: session.token,
                    CONF_ACCOUNT_ID: session.account_id,
                    CONF_COUNTRY_CODE: session.country_code,
                    CONF_REGION: session.region,
                },
            )
            return await request()

    async def _async_update_data(self) -> FryerStatus:
        try:
            status = await self._signed_in(self.client.status)
        except FryerError as err:
            raise UpdateFailed(str(err)) from err

        if self.data is not None:
            self._announce(self.data.state, status.state)
        if status.state in _BUSY:
            self._busy_until = time.monotonic() + ACTIVE_LINGER_SECONDS
        self.update_interval = (
            POLL_ACTIVE if time.monotonic() < self._busy_until else POLL_IDLE
        )
        return status

    def _announce(self, before: str, after: str) -> None:
        """Fire an event when preheat finishes and when a cook finishes."""
        if before == "heating" and after == "preheatEnd":
            self.hass.bus.async_fire(
                EVENT_PREHEAT_DONE, {"entry_id": self.config_entry.entry_id}
            )
        elif before == "cooking" and after in ("cookEnd", "standby"):
            self.hass.bus.async_fire(
                EVENT_COOK_DONE, {"entry_id": self.config_entry.entry_id}
            )

    async def _command(
        self,
        allowed: Callable[[FryerStatus], bool],
        refusal: str,
        action: Callable[[FryerStatus], Awaitable[None]],
    ) -> None:
        """Read the fryer's current state, check it allows the action, then run it."""
        try:
            status = await self._signed_in(self.client.status)
        except FryerError as err:
            raise HomeAssistantError(str(err)) from err
        if not allowed(status):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=refusal,
                translation_placeholders={
                    "state": STATUS.get(status.state, status.state).replace("_", " ")
                },
            )
        try:
            await self._signed_in(lambda: action(status))
        except FryerError as err:
            raise HomeAssistantError(str(err)) from err
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        self._busy_until = time.monotonic() + ACTIVE_LINGER_SECONDS
        await self.async_request_refresh()

    async def preheat(self, temperature: int) -> None:
        """Preheat. The fryer needs a cook queued behind a preheat; Air fry replaces it."""
        minutes = self.minutes
        await self._command(
            lambda s: s.can_preheat,
            "cannot_preheat",
            lambda _: self.client.start(temperature, minutes, preheat=True),
        )

    async def air_fry(self, temperature: int, minutes: int) -> None:
        """Cook now, from idle or after a preheat."""
        await self._command(
            lambda s: s.can_air_fry,
            "cannot_air_fry",
            lambda s: self.client.air_fry(temperature, minutes, clear_first=s.waiting),
        )

    async def stop(self) -> None:
        await self._command(
            lambda s: s.can_stop, "cannot_stop", lambda _: self.client.stop()
        )
