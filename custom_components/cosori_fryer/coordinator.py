"""Serialize device reads and commands without automatic sign-ins."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
        )

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

    async def start_cook(self, temperature: int, minutes: int, preheat: bool) -> None:
        # A positive value enables preheat. The CS358-AF firmware chooses its duration.
        # Use Fahrenheit for commands so pyvesync validates the device's actual step grid.
        async def start() -> bool:
            self.device.temp_unit = TemperatureUnits.FAHRENHEIT
            return await self.device.set_mode(minutes * 60, temperature, preheat_time=1 if preheat else 0)

        await self.command({"standby", "cookEnd"}, start)

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

    async def start_from_preheat(self) -> None:
        await self.command({"preheatEnd"}, self.device.cook_from_preheat, "not_preheated")
