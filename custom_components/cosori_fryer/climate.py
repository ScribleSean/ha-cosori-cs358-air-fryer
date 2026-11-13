"""The fryer as a heater: off or on, a target temperature, and what it is doing."""

from __future__ import annotations

import asyncio
import contextlib
import math
from typing import Any, ClassVar

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import DOMAIN, IDLE, STATUS
from .entity import FryerEntity

# Commands are serialized by the coordinator. A limit here would also queue the
# temperature HomeKit sends behind the mode change it sends first.
PARALLEL_UPDATES = 0
# HomeKit sends a mode change and a temperature as two calls. Waiting this long
# before starting lets "set to 360" and "turn on" from one request heat to 360.
START_DELAY = 1.0
ACTIONS = {
    "heating": HVACAction.PREHEATING,
    "cooking": HVACAction.HEATING,
    "keeping_warm": HVACAction.HEATING,
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FryerClimate(entry.runtime_data)])


class FryerClimate(FryerEntity, ClimateEntity, RestoreEntity):
    """Heat preheats to the target and holds it there; off stops anything."""

    _attr_name = None
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_precision = PRECISION_WHOLE
    _attr_min_temp = 170
    _attr_max_temp = 400
    _unrecorded_attributes = frozenset({"time_left"})

    def __init__(self, coordinator):
        super().__init__(coordinator, "fryer")
        # Waiting out START_DELAY, then sending the start; both show as On.
        self._starting = False
        self._sending: asyncio.Event | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and isinstance(
            temperature := last.attributes.get(ATTR_TEMPERATURE), int | float
        ):
            # Saved states hold the temperature in the Home Assistant unit.
            temperature = TemperatureConverter.convert(
                temperature, self.hass.config.units.temperature_unit, UnitOfTemperature.FAHRENHEIT
            )
            self.coordinator.update_settings(temperature=self._step(temperature))

    async def async_will_remove_from_hass(self) -> None:
        self._starting = False
        await super().async_will_remove_from_hass()

    @property
    def target_temperature_step(self) -> float:
        # Celsius dashboards get the fryer's own 5 °C grid; °F uses 10 °F.
        return 5 if self.hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS else 10

    @property
    def _running(self) -> bool:
        return self.coordinator.data.status not in IDLE

    @property
    def _pending(self) -> bool:
        return self._starting or self._sending is not None

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._running or self._pending else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if not self._running:
            return HVACAction.IDLE if self._pending else HVACAction.OFF
        return ACTIONS.get(STATUS.get(self.coordinator.data.status), HVACAction.IDLE)

    @property
    def target_temperature(self) -> float:
        data = self.coordinator.data
        if self._running and data.cook_temp_f:
            return data.cook_temp_f
        return self.coordinator.settings.temperature

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        starting = self._pending and not self._running
        attributes = {"cook_status": "starting" if starting else STATUS.get(data.status)}
        if data.time_left:
            attributes["time_left"] = f"{math.ceil(data.time_left / 60)} min"
        return attributes

    @staticmethod
    def _step(temperature: float) -> int:
        return min(400, max(170, round(temperature / 10) * 10))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        hvac_mode = kwargs.get("hvac_mode")
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if hvac_mode == HVACMode.OFF:
            # Off always stops; the temperature is kept for next time.
            await self.async_set_hvac_mode(hvac_mode)
            if temperature is not None:
                self.coordinator.update_settings(temperature=self._step(temperature))
            return
        if temperature is not None:
            temperature = self._step(temperature)
            changed = temperature != self.target_temperature
            if changed and self.coordinator.data.held and self._sending is None:
                # A hold has no food in yet, so move it. A fryer stopped since the
                # last poll stays off.
                await self.coordinator.hold(temperature, only_move=True)
                self.coordinator.update_settings(temperature=temperature)
                if hvac_mode is None:
                    return
            elif changed and (self._running or self._sending is not None):
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="running")
            else:
                self.coordinator.update_settings(temperature=temperature)
        if hvac_mode is not None:
            await self.async_set_hvac_mode(hvac_mode)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            if self._starting:
                self._starting = False
                self.async_write_ha_state()
                return
            if self._sending is not None:
                # The start is already on its way; stop once it lands.
                await self._sending.wait()
            # The last poll can be a minute old, so always ask: stop reads fresh state,
            # ends a cook started at the fryer, and refuses when it is idle.
            with contextlib.suppress(ServiceValidationError):
                await self.coordinator.stop()
            return
        # Already on: a hold, a cook, or a preheat waiting for Start. Start cooks, not this.
        if self._running or self._pending:
            return
        self._starting = True
        self.async_write_ha_state()
        try:
            await asyncio.sleep(START_DELAY)
            if not self._starting:
                return
            self._starting = False
            self._sending = asyncio.Event()
            await self.coordinator.hold()
        finally:
            self._starting = False
            if self._sending is not None:
                self._sending.set()
                self._sending = None
            self.async_write_ha_state()
