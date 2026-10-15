"""Polling, session handling and commands for the fryer."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.hass_dict import HassKey

from .api import AuthError, FryerClient, FryerError, FryerStatus, TokenRejected
from .const import (
    ACTIVE_LINGER_SECONDS,
    CONF_ACCOUNT_ID,
    CONF_COUNTRY_CODE,
    CONF_LAST_LOGIN,
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
_BUSY = {"heating", "preheatStop", "preheatEnd", "ready", "cooking", "cookStop", "keeping"}

type FryerConfigEntry = ConfigEntry[FryerCoordinator]

# Login attempts by email, shared by the setup and reauth forms and the coordinator.
_LOGINS: HassKey[dict[str, float]] = HassKey(f"{DOMAIN}_logins")


def last_login(hass: HomeAssistant, email: str, entry: ConfigEntry | None = None) -> float | None:
    """When this account last tried to sign in, here or before a restart."""
    times = [hass.data.get(_LOGINS, {}).get(email.lower())]
    if entry is not None:
        times.append(entry.data.get(CONF_LAST_LOGIN))
    return max((t for t in times if t is not None), default=None)


def login_allowed(hass: HomeAssistant, email: str, entry: ConfigEntry | None = None) -> bool:
    last = last_login(hass, email, entry)
    return last is None or time.time() - last >= RELOGIN_COOLDOWN_SECONDS


def note_login(hass: HomeAssistant, email: str, entry: ConfigEntry | None = None) -> None:
    """Record an attempt before making it: failed and rate-limited ones need the same cooldown."""
    now = time.time()
    hass.data.setdefault(_LOGINS, {})[email.lower()] = now
    if entry is not None:
        # Wall-clock time in the entry survives Home Assistant restarts.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_LAST_LOGIN: now})


def forget_login(hass: HomeAssistant, email: str, entry: ConfigEntry | None = None) -> None:
    """A rejected password is a definite answer, so a new one can be tried straight away."""
    hass.data.get(_LOGINS, {}).pop(email.lower(), None)
    if entry is not None and CONF_LAST_LOGIN in entry.data:
        data = {key: value for key, value in entry.data.items() if key != CONF_LAST_LOGIN}
        hass.config_entries.async_update_entry(entry, data=data)


class FryerCoordinator(DataUpdateCoordinator[FryerStatus]):
    """Reads the fryer every 30 s while busy and every minute in standby."""

    config_entry: FryerConfigEntry

    def __init__(self, hass: HomeAssistant, entry: FryerConfigEntry, client: FryerClient) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_IDLE, always_update=False
        )
        self.client = client
        # The values the Preheat and Start buttons use, set by the number entities.
        self.temperature = DEFAULT_TEMPERATURE
        self.minutes = DEFAULT_MINUTES
        self._busy_until = 0.0
        self._cycle: str | None = None  # "preheat" or "cook" while one is in progress
        self._login_lock = asyncio.Lock()
        self._password_rejected = False  # Until reauth reloads the entry with a new one.
        # Polls and commands read the fryer one at a time, so states are seen in order.
        self._read_lock = asyncio.Lock()

    async def _async_setup(self) -> None:
        """Find the fryer once, before the first status read."""
        try:
            await self._signed_in(self.client.find_fryer)
        except FryerError as err:
            raise UpdateFailed(str(err)) from err
        session = self.client.session
        if session is None or self.config_entry.unique_id == session.account_id:
            return
        # Entries from before 0.3.0 used the email as their unique id.
        other = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, session.account_id)
        if other is None:
            self.hass.config_entries.async_update_entry(self.config_entry, unique_id=session.account_id)

    async def _signed_in[T](self, request: Callable[[], Awaitable[T]]) -> T:
        """Run a request, signing in again once if VeSync rejects the token."""
        token = self.client.session.token if self.client.session else None
        try:
            return await request()
        except TokenRejected:
            await self._sign_in_again(token)
            return await request()

    async def _sign_in_again(self, rejected_token: str | None) -> None:
        async with self._login_lock:
            current = self.client.session.token if self.client.session else None
            if current and current != rejected_token:
                return  # Someone else already signed in while we waited.
            if self._password_rejected:
                raise ConfigEntryAuthFailed("VeSync rejected the stored password")
            email = self.config_entry.data[CONF_EMAIL]
            if not login_allowed(self.hass, email, self.config_entry):
                raise FryerError("Signed out by another VeSync login; trying again shortly")
            note_login(self.hass, email, self.config_entry)
            try:
                session = await self.client.login()
            except AuthError as err:
                self._password_rejected = True
                # The reauth form can try a new password straight away.
                forget_login(self.hass, email, self.config_entry)
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

    async def _async_update_data(self) -> FryerStatus:
        async with self._read_lock:
            try:
                return await self._read()
            except FryerError as err:
                raise UpdateFailed(str(err)) from err

    async def _read(self) -> FryerStatus:
        """Read the fryer and act on what it says. Call with the read lock held."""
        status = await self._signed_in(self.client.status)
        self._track(status.state)
        if status.state in _BUSY:
            self._busy_until = time.monotonic() + ACTIVE_LINGER_SECONDS
        self.update_interval = POLL_ACTIVE if time.monotonic() < self._busy_until else POLL_IDLE
        return status

    def _track(self, state: str) -> None:
        """Fire an event when a preheat or a cook runs to the end.

        The cycle is remembered through pauses and a pulled drawer, so a finish seen
        after an interruption still counts. A stopped program goes to standby and
        fires nothing, and a program already finished at startup isn't announced.
        """
        if state in ("heating", "preheatStop"):
            self._cycle = "preheat"
        elif state in ("cooking", "cookStop"):
            self._cycle = "cook"
        elif state == "preheatEnd" and self._cycle == "preheat":
            self._cycle = None
            self._fire(EVENT_PREHEAT_DONE)
        elif state == "cookEnd" and self._cycle == "cook":
            self._cycle = None
            self._fire(EVENT_COOK_DONE)
        elif state == "standby":
            self._cycle = None

    def _fire(self, event: str) -> None:
        data = {"entry_id": self.config_entry.entry_id}
        if self.client.device:
            device = dr.async_get(self.hass).async_get_device({(DOMAIN, self.client.device.cid)})
            if device:
                data["device_id"] = device.id
        self.hass.bus.async_fire(event, data)

    async def _command(
        self,
        allowed: Callable[[FryerStatus], bool],
        refusal: str,
        action: Callable[[FryerStatus], Awaitable[None]],
    ) -> None:
        """Read the fryer's state, check it allows the action, then run it, one command at a time."""
        async with self._read_lock:
            try:
                status = await self._read()
                # Show what was just read, even if the command is refused.
                self.async_set_updated_data(status)
                if not allowed(status):
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key=refusal,
                        translation_placeholders={
                            "state": STATUS.get(status.state, status.state).replace("_", " ")
                        },
                    )
                await self._signed_in(lambda: action(status))
            except ConfigEntryAuthFailed as err:
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
            except FryerError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="command_failed",
                    translation_placeholders={"error": str(err)},
                ) from err
        self._busy_until = time.monotonic() + ACTIVE_LINGER_SECONDS
        await self.async_request_refresh()

    async def start_cooking(self, temperature: int, minutes: int, *, preheat: bool) -> None:
        """Start a manual program, optionally preheating first."""
        await self._command(
            lambda s: s.can_preheat if preheat else s.idle,
            "cannot_preheat" if preheat else "cannot_start",
            lambda _: self.client.start(temperature, minutes, preheat=preheat),
        )

    async def press_start(self) -> None:
        """The fryer's Start button: cook from standby, or continue once preheated."""

        async def start(status: FryerStatus) -> None:
            if status.preheated:
                await self.client.continue_after_preheat()
            else:
                await self.client.start(self.temperature, self.minutes, preheat=False)

        await self._command(lambda s: s.can_start, "cannot_start", start)

    async def stop(self) -> None:
        await self._command(lambda s: s.can_stop, "cannot_stop", lambda _: self.client.stop())
