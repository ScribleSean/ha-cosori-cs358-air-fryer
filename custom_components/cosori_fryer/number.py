"""The cooking temperature and time used by the Preheat and Start buttons.

These are settings kept in Home Assistant; changing them sends nothing to the fryer.
"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntityDescription, RestoreNumber
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from .api import MINUTES_MAX, MINUTES_MIN, TEMP_MAX, TEMP_MIN, TEMP_STEP
from .coordinator import FryerConfigEntry, FryerCoordinator
from .entity import FryerEntity

PARALLEL_UPDATES = 0

# Entity key -> coordinator attribute holding the value.
_FIELD = {"cook_temperature": "temperature", "cook_time": "minutes"}

NUMBERS = (
    NumberEntityDescription(
        key="cook_temperature",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_min_value=TEMP_MIN,
        native_max_value=TEMP_MAX,
        native_step=TEMP_STEP,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    ),
    NumberEntityDescription(
        key="cook_time",
        native_min_value=MINUTES_MIN,
        native_max_value=MINUTES_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FryerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(FryerSetting(entry.runtime_data, description) for description in NUMBERS)


class FryerSetting(FryerEntity, RestoreNumber):
    def __init__(self, coordinator: FryerCoordinator, description: NumberEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return True

    @property
    def native_step(self) -> float | None:
        step = self.entity_description.native_step
        native, shown = self.native_unit_of_measurement, self.unit_of_measurement
        if step and self.device_class == NumberDeviceClass.TEMPERATURE and native != shown:
            # Home Assistant converts the value and limits to Celsius but not the step.
            return TemperatureConverter.convert_interval(step, native, shown)
        return step

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is None or last.native_value is None:
            return
        value = last.native_value
        step = self.entity_description.native_step or 1
        if self.native_min_value <= value <= self.native_max_value and value % step == 0:
            setattr(self.coordinator, _FIELD[self.entity_description.key], int(value))

    @property
    def native_value(self) -> float:
        return getattr(self.coordinator, _FIELD[self.entity_description.key])

    async def async_set_native_value(self, value: float) -> None:
        # Home Assistant checks the range but not the step; snap to the fryer's steps.
        step = self.entity_description.native_step or 1
        snapped = round(value / step) * step
        snapped = min(max(snapped, self.native_min_value), self.native_max_value)
        setattr(self.coordinator, _FIELD[self.entity_description.key], int(snapped))
        self.async_write_ha_state()
