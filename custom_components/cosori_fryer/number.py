"""Cook time for the next cook."""

from homeassistant.components.number import NumberDeviceClass, NumberMode, RestoreNumber
from homeassistant.const import UnitOfTime

from .entity import FryerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FryerCookTime(entry.runtime_data)])


class FryerCookTime(FryerEntity, RestoreNumber):
    """Stored in Home Assistant and sent with the next Start or Preheat."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator):
        super().__init__(coordinator, "cook_minutes")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) and last.native_value is not None:
            self.coordinator.update_settings(minutes=int(min(60, max(1, last.native_value))))

    @property
    def native_value(self) -> int:
        return self.coordinator.settings.minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.update_settings(minutes=int(value))
