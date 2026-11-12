"""Status, measured temperatures and phase end timestamps."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature

from .const import STATUS
from .coordinator import FryerData
from .entity import FryerEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FryerSensorDescription(SensorEntityDescription):
    """A value from an immutable coordinator snapshot."""

    value_fn: Callable[[FryerData], str | float | datetime | None]


SENSORS = (
    FryerSensorDescription(
        key="cook_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(STATUS.values()),
        value_fn=lambda d: STATUS.get(d.status),
    ),
    FryerSensorDescription(
        key="current_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.current_temp,
    ),
    FryerSensorDescription(
        key="cook_set_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.cook_set_temp,
    ),
    FryerSensorDescription(
        key="preheat_ends_at", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda d: d.preheat_ends_at
    ),
    FryerSensorDescription(
        key="cook_ends_at", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda d: d.cook_ends_at
    ),
)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(FryerSensor(entry.runtime_data, description) for description in SENSORS)


class FryerSensor(FryerEntity, SensorEntity):
    """A fryer sensor following core VeSync entity descriptions."""

    def __init__(self, coordinator, description):
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)
