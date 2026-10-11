"""Read-only fryer sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FryerConfigEntry, FryerCoordinator
from .cosori358 import PREHEAT_DONE, PREHEAT_PAUSED, PREHEATING, FryerStatus
from .entity import FryerEntity

_PREHEAT_STATES = {PREHEATING, PREHEAT_PAUSED, PREHEAT_DONE}


def _target_temp(status: FryerStatus) -> int | None:
    if status.cook_status in _PREHEAT_STATES and status.preheat_temp:
        return status.preheat_temp
    return status.cook_temp


@dataclass(frozen=True, kw_only=True)
class FryerSensorDescription(SensorEntityDescription):
    value: Callable[[FryerStatus], int | str | None]
    is_temperature: bool = False


SENSORS = (
    FryerSensorDescription(key="status", value=lambda s: s.cook_status),
    FryerSensorDescription(
        key="current_temp", value=lambda s: s.current_temp, is_temperature=True
    ),
    FryerSensorDescription(key="target_temp", value=_target_temp, is_temperature=True),
    FryerSensorDescription(
        key="time_remaining",
        value=lambda s: s.total_seconds_left,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FryerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(FryerSensor(entry.runtime_data, d) for d in SENSORS)


class FryerSensor(FryerEntity, SensorEntity):
    entity_description: FryerSensorDescription

    def __init__(
        self, coordinator: FryerCoordinator, description: FryerSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.is_temperature:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.is_temperature:
            if self.coordinator.data.temp_unit == "c":
                return UnitOfTemperature.CELSIUS
            return UnitOfTemperature.FAHRENHEIT
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> int | str | None:
        return self.entity_description.value(self.coordinator.data)
