"""The temperature and cook time the Preheat button will use.

These are settings kept in Home Assistant. Changing them sends nothing to the
fryer.
"""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FryerConfigEntry, FryerCoordinator
from .cosori358 import MAX_MINUTES, MIN_MINUTES, TEMP_RANGE
from .entity import FryerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FryerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([TargetTempNumber(coordinator), CookMinutesNumber(coordinator)])


class _SettingNumber(FryerEntity, RestoreNumber):
    _attr_mode = NumberMode.BOX
    _field: str

    def __init__(self, coordinator: FryerCoordinator, key: str) -> None:
        super().__init__(coordinator, key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            value = int(last.native_value)
            if self.native_min_value <= value <= self.native_max_value:
                setattr(self.coordinator, self._field, value)

    @property
    def native_value(self) -> float:
        return getattr(self.coordinator, self._field)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self.coordinator, self._field, int(value))
        self.async_write_ha_state()


class TargetTempNumber(_SettingNumber):
    _field = "target_temp"
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, coordinator: FryerCoordinator) -> None:
        super().__init__(coordinator, "target_temp")
        self._attr_native_min_value, self._attr_native_max_value = TEMP_RANGE["f"]


class CookMinutesNumber(_SettingNumber):
    _field = "cook_minutes"
    _attr_native_step = 1
    _attr_native_min_value = MIN_MINUTES
    _attr_native_max_value = MAX_MINUTES
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: FryerCoordinator) -> None:
        super().__init__(coordinator, "cook_minutes")
