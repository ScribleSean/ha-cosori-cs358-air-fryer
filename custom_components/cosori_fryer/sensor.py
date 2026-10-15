"""Fryer sensors: status, set temperature and time remaining."""

from __future__ import annotations

import math
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

from .api import FryerStatus
from .const import STATUS
from .coordinator import FryerConfigEntry, FryerCoordinator
from .entity import FryerEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FryerSensorDescription(SensorEntityDescription):
    value: Callable[[FryerStatus], str | int | None]


SENSORS = (
    FryerSensorDescription(
        key="cook_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(STATUS.values()),
        value=lambda s: STATUS.get(s.state),
    ),
    FryerSensorDescription(
        key="set_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value=lambda s: None if s.idle else s.target_temp,
    ),
    FryerSensorDescription(
        key="time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value=lambda s: math.ceil(s.seconds_left / 60),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FryerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        FryerSensor(entry.runtime_data, description) for description in SENSORS
    )


class FryerSensor(FryerEntity, SensorEntity):
    entity_description: FryerSensorDescription

    def __init__(
        self, coordinator: FryerCoordinator, description: FryerSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | None:
        return self.entity_description.value(self.coordinator.data)
