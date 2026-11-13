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

from .const import DOMAIN, POLL_ACTIVE, POLL_IDLE
from .manager import FryerManager

_LOGGER = logging.getLogger(__name__)
AUTH_ERRORS = (VeSyncTokenError, VeSyncLoginError)
READ_ERRORS = (VeSyncError, ClientError, TimeoutError, ValueError)


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

    async def _async_update_data(self) -> FryerData:
        async with self._lock:
            return await self._read()

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

    async def command(
        self, allowed: set[str], action: Callable[[], Awaitable[bool]], refusal: str = "command_refused"
    ) -> None:
        """Check fresh state; a refusal does not change read availability."""
        async with self._lock:
            try:
                data = await self._read()
                self.async_set_updated_data(data)
                if data.status not in allowed:
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key=refusal)
                async with asyncio.timeout(15):
                    success = await action()
                response = self.device.last_response
                outer = (
                    response.response_data.get("result", {})
                    if response and isinstance(response.response_data, dict)
                    else {}
                )
                if not success or not isinstance(outer, dict) or outer.get("code") != 0:
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

    async def start_cook(self, temperature: int, minutes: int, preheat: bool) -> None:
        # Kept only once the fryer accepts it, so a refused call leaves the settings alone.
        settings = CookSettings(temperature=temperature, minutes=minutes)
        await self.start(preheat, after_preheat=False, settings=settings)
        self.update_settings(temperature=temperature, minutes=minutes, preset="custom")

    async def start(
        self, preheat: bool = False, after_preheat: bool = True, settings: CookSettings | None = None
    ) -> None:
        """Cook now, preheat first, or continue a finished preheat, with the current settings."""

        # A positive value enables preheat. The CS358-AF firmware chooses its duration.
        # Use Fahrenheit for commands so pyvesync validates the device's actual step grid.
        async def send() -> bool:
            if not preheat and self.data.status == "preheatEnd":
                return await self.device.cook_from_preheat()
            chosen = settings or self.settings
            self.device.temp_unit = TemperatureUnits.FAHRENHEIT
            return await self.device.set_mode(
                chosen.minutes * 60,
                chosen.temperature,
                preheat_time=1 if preheat else 0,
                cook_mode=None if chosen.preset == "custom" else chosen.preset,
            )

        allowed = {"standby", "cookEnd"}
        if after_preheat and not preheat:
            allowed.add("preheatEnd")
        await self.command(allowed, send, "busy")

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
