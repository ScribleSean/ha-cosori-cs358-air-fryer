"""Start, preheat and stop, like the buttons on the fryer."""

from homeassistant.components.button import ButtonEntity

from .entity import FryerEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(FryerButton(entry.runtime_data, key) for key in ("start", "preheat", "stop"))


class FryerButton(FryerEntity, ButtonEntity):
    """Refuse invalid commands without claiming the device is offline."""

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = key

    async def async_press(self):
        if self.key == "stop":
            await self.coordinator.stop()
        else:
            await self.coordinator.start(preheat=self.key == "preheat")
