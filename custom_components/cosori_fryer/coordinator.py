"""Serialize device reads and commands without automatic sign-ins."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from custom_components.cosori_fryer._vendor.pyvesync.const import ConnectionStatus, TemperatureUnits
from custom_components.cosori_fryer._vendor.pyvesync.devices.vesynckitchen import VeSyncTurboBlazeFryer
from custom_components.cosori_fryer._vendor.pyvesync.utils.errors import (
    VeSyncError,
    VeSyncLoginError,
    VeSyncTokenError,
)

from . import planner
from .const import DOMAIN, IDLE, POLL_ACTIVE, POLL_IDLE
from .manager import FryerManager

_LOGGER = logging.getLogger(__name__)
AUTH_ERRORS = (VeSyncTokenError, VeSyncLoginError)
READ_ERRORS = (VeSyncError, ClientError, TimeoutError, ValueError)
# Phases of a preheat. A hold is a preheat with the placeholder cook step behind it.
PREHEAT = ("heating", "preheatStop", "preheatEnd")
# After ending a hold, read this many times, this far apart, for the cloud to report idle.
CLEAR_POLLS = 5
CLEAR_DELAY = 1.0
# When stopping a hold at its limit fails, try again this often, this many times.
LIMIT_RETRY_SECONDS = 60
LIMIT_RETRIES = 10


@dataclass(frozen=True)
class FryerData:
    """Snapshot values so pyvesync mutations cannot alter an earlier poll."""

    status: str
    current_temp: float | None
    cook_set_temp: float | None
    preheat_ends_at: datetime | None
    cook_ends_at: datetime | None
    finish: str | None
    finish_sequence: int
    cook_temp_f: int | None = None
    time_left: int | None = None
    # Preheated or preheating with no food queued, as opposed to a real cook.
    held: bool = False


@dataclass
class CookSettings:
    """What the next Start or Preheat sends. Changing these sends nothing."""

    temperature: int = 350
    minutes: int = 15
    preset: str = "custom"


type FryerConfigEntry = ConfigEntry[FryerCoordinator]


class FryerCoordinator(DataUpdateCoordinator[FryerData]):
    """Poll one CS358-AF and report only read failures as unavailable."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FryerConfigEntry,
        manager: FryerManager,
        device: VeSyncTurboBlazeFryer,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_IDLE)
        self.manager = manager
        self.device = device
        self._lock = asyncio.Lock()
        self._cycle: str | None = None
        self._finish: str | None = None
        self._sequence = 0
        self.settings = CookSettings()
        self._hold_limit: Callable[[], None] | None = None
        # Set once the limit has passed and until the hold is stopped or replaced.
        self._limit_passed = False
        self._limit_tries = 0

    async def async_shutdown(self) -> None:
        """Runs on unload and on Home Assistant stop."""
        self._cancel_hold_limit()
        await super().async_shutdown()

    async def _async_update_data(self) -> FryerData:
        async with self._lock:
            data = await self._read()
        # A hold found by a poll (after a restart, say) still gets its limit. One whose
        # limit has already passed only gets another try at stopping it.
        if data.held and self._hold_limit is None:
            if self._limit_passed:
                self._retry_hold_limit()
            else:
                self._schedule_hold_limit()
        return data

    async def _read(self) -> FryerData:
        try:
            async with asyncio.timeout(15):
                await self.device.update()
            if self.device.state.connection_status == ConnectionStatus.OFFLINE:
                raise UpdateFailed("Fryer is offline")
            if self.device.last_response is None or self.device.last_response.code != 0:
                raise UpdateFailed("Fryer status could not be read")
        except AUTH_ERRORS as err:
            raise ConfigEntryAuthFailed from err
        except READ_ERRORS as err:
            raise UpdateFailed("Fryer status could not be read") from err
        state = self.device.state
        # The fork maps ready to cookStop and keeping to cooking. Preserve
        # these distinct phases from the saved response.
        response = self.device.last_response.response_data
        outer = response.get("result", {}) if isinstance(response, dict) else {}
        if not isinstance(outer, dict):
            raise UpdateFailed("Fryer status could not be read")
        reported = outer.get("result", {})
        if outer.get("code") != 0 or not isinstance(reported, dict) or "cookStatus" not in reported:
            raise UpdateFailed("Fryer status could not be read")
        raw_status = reported["cookStatus"]
        status = {"ready": "ready", "keeping": "keeping_warm"}.get(raw_status, state.cook_status)
        if status in ("heating", "preheatStop"):
            self._cycle = "preheat"
        elif status in ("cooking", "cookStop"):
            self._cycle = "cook"
        elif (status == "preheatEnd" and self._cycle == "preheat") or (
            status == "cookEnd" and self._cycle == "cook"
        ):
            self._finish = f"{self._cycle}_finished"
            self._sequence += 1
            self._cycle = None
        elif status in ("standby", "ready"):
            self._cycle = None
        self.update_interval = POLL_ACTIVE if state.is_running else POLL_IDLE
        now = dt_util.utcnow()

        def stable_end(seconds, previous):
            if seconds is None:
                return None
            end = now + timedelta(seconds=seconds)
            if previous is not None and abs((end - previous).total_seconds()) <= 5:
                return previous
            return end

        previous = self.data
        if status in ("heating", "preheatStop") or (status == "pullOut" and self._cycle == "preheat"):
            time_left = state.preheat_time_remaining
        elif status in ("cooking", "cookStop", "pullOut"):
            time_left = state.cook_time_remaining
        else:
            time_left = None
        # Firmware 2.0.06 reports a constant 0 instead of a measurement.
        current_temp = self._celsius(state.current_temp) if reported.get("currentTemp") else None
        return FryerData(
            status=status,
            current_temp=current_temp,
            cook_set_temp=self._celsius(state.cook_set_temp),
            preheat_ends_at=stable_end(
                state.preheat_time_remaining, previous.preheat_ends_at if previous else None
            )
            if status == "heating" and state.preheat_time_remaining is not None
            else None,
            cook_ends_at=stable_end(state.cook_time_remaining, previous.cook_ends_at if previous else None)
            if status == "cooking" and state.cook_time_remaining is not None
            else None,
            finish=self._finish,
            finish_sequence=self._sequence,
            cook_temp_f=self._fahrenheit(state.cook_set_temp),
            time_left=time_left,
            held=status in PREHEAT
            and bool(state.preheat_set_time)
            and state.cook_set_time == planner.HOLD_STEP_SECONDS,
        )

    def _fahrenheit(self, value: int | None) -> int | None:
        if not value:
            return None
        if self.device.temp_unit != TemperatureUnits.FAHRENHEIT:
            value = TemperatureConverter.convert(value, "°C", "°F")
        return round(value / 10) * 10

    def _celsius(self, value: int | None) -> float | None:
        if value is None:
            return None
        return (
            TemperatureConverter.convert(value, "°F", "°C")
            if self.device.temp_unit == TemperatureUnits.FAHRENHEIT
            else value
        )

    def _accepted(self, success: bool) -> bool:
        """Whether the last command succeeded and the cloud reported no error."""
        response = self.device.last_response
        outer = (
            response.response_data.get("result", {})
            if response and isinstance(response.response_data, dict)
            else {}
        )
        return bool(success) and isinstance(outer, dict) and outer.get("code") == 0

    async def command(
        self,
        allowed: set[str] | Callable[[FryerData], bool],
        action: Callable[[], Awaitable[bool]],
        refusal: str = "command_refused",
    ) -> None:
        """Check fresh state; a refusal does not change read availability."""
        async with self._lock:
            try:
                data = await self._read()
                self.async_set_updated_data(data)
                if not (allowed(data) if callable(allowed) else data.status in allowed):
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key=refusal)
                async with asyncio.timeout(90):
                    success = await action()
                if not self._accepted(success):
                    raise HomeAssistantError(translation_domain=DOMAIN, translation_key="command_refused")
            except (ConfigEntryAuthFailed, *AUTH_ERRORS) as err:
                self.async_set_update_error(err)
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
            except (UpdateFailed, *READ_ERRORS) as err:
                self.async_set_update_error(err)
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="command_failed") from err
        await self.async_request_refresh()

    @callback
    def update_settings(self, **changes) -> None:
        """Change what the next cook sends and refresh the entities that show it."""
        for key, value in changes.items():
            setattr(self.settings, key, value)
        self.async_update_listeners()

    @callback
    def choose_preset(self, preset: str) -> None:
        """Pick a fryer program and load its default temperature and time."""
        if preset == "custom":
            self.update_settings(preset=preset)
            return
        mode = self.device.get_cook_mode(preset)
        self.update_settings(
            preset=preset,
            temperature=mode.default_temp_f,
            minutes=min(60, max(1, round(mode.cook_time / 60))),
        )

    async def _send(self, seconds: int, temperature: int, preheat: bool, preset: str = "custom") -> bool:
        # A positive value enables preheat. The CS358-AF firmware chooses its duration.
        # Use Fahrenheit for commands so pyvesync validates the device's actual step grid.
        self.device.temp_unit = TemperatureUnits.FAHRENHEIT
        return await self.device.set_mode(
            seconds,
            temperature,
            preheat_time=1 if preheat else 0,
            cook_mode=None if preset == "custom" else preset,
        )

    async def _end_hold(self) -> bool:
        """End a hold, if one is on, and confirm from a fresh read that the fryer is idle."""
        if not self.data.held:
            return True
        if not self._accepted(await self.device.end()):
            return False
        for attempt in range(CLEAR_POLLS):
            if attempt:
                await asyncio.sleep(CLEAR_DELAY)
            data = await self._read()
            self.async_set_updated_data(data)
            if data.status in IDLE:
                return True
        return False

    @staticmethod
    def _idle_or_held(data: FryerData) -> bool:
        return data.status in IDLE or data.held

    async def hold(self, temperature: int | None = None, *, only_move: bool = False) -> None:
        """Preheat to a temperature and hold there until a cook starts or the limit passes.

        With only_move, an existing hold moves to the temperature and an idle fryer is left off.
        """
        sent = found = False

        async def send() -> bool:
            nonlocal sent, found
            found = self.data.held
            target = temperature or self.settings.temperature
            if (found and self.data.cook_temp_f == target) or (only_move and not found):
                return True
            sent = True
            return await self._end_hold() and await self._send(planner.HOLD_STEP_SECONDS, target, True)

        await self.command(self._idle_or_held, send, "running" if only_move else "busy")
        if sent:
            self._schedule_hold_limit()
        elif not found:
            self._clear_hold_limit()
        elif self._hold_limit is None and not self._limit_passed:
            self._schedule_hold_limit()

    async def start(self) -> None:
        """Continue a finished preheat, or cook now with the current settings."""

        async def send() -> bool:
            if self.data.status == "preheatEnd" and not self.data.held:
                return await self.device.cook_from_preheat()
            chosen = self.settings
            return await self._end_hold() and await self._send(
                chosen.minutes * 60, chosen.temperature, False, chosen.preset
            )

        await self.command(lambda data: self._idle_or_held(data) or data.status == "preheatEnd", send, "busy")
        self._clear_hold_limit()

    async def cook(self, temperature: int, minutes: int, preheat: bool) -> None:
        """Cook at a temperature for a time, preheating first when asked. Replaces a hold."""

        async def send() -> bool:
            return await self._end_hold() and await self._send(minutes * 60, temperature, preheat)

        await self.command(self._idle_or_held, send, "busy")
        self._clear_hold_limit()
        # Kept only once the fryer accepts it, so a refused call leaves the settings alone.
        self.update_settings(temperature=temperature, minutes=minutes, preset="custom")

    async def keep_warm(self, minutes: object = None) -> None:
        """A plain cook at the lowest setting. Replaces a hold."""
        plan = make_plan(planner.Intent("keep_warm", minutes=minutes))

        async def send() -> bool:
            return await self._end_hold() and await self._send(plan.minutes * 60, plan.temp_f, False)

        await self.command(self._idle_or_held, send, "busy")
        self._clear_hold_limit()
        _LOGGER.info("Keeping warm at %s °F for %s minutes", plan.temp_f, plan.minutes)

    async def stop(self) -> None:
        await self.command(
            {
                "heating",
                "preheatStop",
                "preheatEnd",
                "cooking",
                "cookStop",
                "pullOut",
                "ready",
                "keeping_warm",
            },
            self.device.end,
        )
        self._clear_hold_limit()

    @callback
    def _schedule_hold_limit(self) -> None:
        """Start a new hold's limit."""
        self._clear_hold_limit()
        self._hold_limit = async_call_later(self.hass, planner.HOLD_LIMIT_MIN * 60, self._hold_limit_reached)

    @callback
    def _retry_hold_limit(self) -> None:
        self._cancel_hold_limit()
        self._hold_limit = async_call_later(self.hass, LIMIT_RETRY_SECONDS, self._hold_limit_reached)

    @callback
    def _cancel_hold_limit(self) -> None:
        if self._hold_limit is not None:
            self._hold_limit()
            self._hold_limit = None

    @callback
    def _clear_hold_limit(self) -> None:
        """No hold any more: forget its limit."""
        self._cancel_hold_limit()
        self._limit_passed = False
        self._limit_tries = 0

    async def _hold_limit_reached(self, _now: datetime) -> None:
        """Stop a hold that no cook has replaced; leave anything else running."""
        # Marked before waiting for the lock, so a poll finishing now does not restart the limit.
        self._limit_passed = True
        self._hold_limit = None
        try:
            await self.command(lambda data: data.held, self.device.end)
        except ServiceValidationError:
            if self.data.status == "pullOut":
                # The basket is out, so the hold may come back: look again in a minute.
                self._retry_hold_limit()
                return
            # Read fine and nothing is holding: a cook replaced it or it was stopped.
            self._clear_hold_limit()
        except HomeAssistantError as err:
            self._limit_tries += 1
            if self._limit_tries < LIMIT_RETRIES:
                _LOGGER.warning("Could not stop a hold at its limit; trying again in a minute: %s", err)
                self._retry_hold_limit()
            else:
                # A later poll that still finds the hold tries again.
                _LOGGER.warning(
                    "Could not stop a hold at its limit after %s tries: %s", self._limit_tries, err
                )
                self._limit_tries = 0
        else:
            self._clear_hold_limit()
            _LOGGER.info("Stopped a hold after %s minutes with no cook", planner.HOLD_LIMIT_MIN)


def make_plan(intent: planner.Intent) -> planner.Plan:
    """Plan a request, refusing values the fryer can't run and logging any rounding."""
    try:
        plan = planner.plan(intent)
    except planner.PlanError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_request",
            translation_placeholders={"reason": str(err)},
        ) from err
    if plan.question:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_request",
            translation_placeholders={"reason": plan.question},
        )
    for note in plan.notes:
        _LOGGER.info("%s", note)
    return plan
