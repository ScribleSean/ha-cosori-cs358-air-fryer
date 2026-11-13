"""On once a preheat has finished and the fryer waits for food."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import FryerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FryerPreheated(entry.runtime_data)])


class FryerPreheated(FryerEntity, BinarySensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "preheated")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.status == "preheatEnd"
