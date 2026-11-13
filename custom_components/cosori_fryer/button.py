"""Start, preheat, keep warm and stop."""

from homeassistant.components.button import ButtonEntity

from .entity import FryerEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        FryerButton(entry.runtime_data, key) for key in ("start", "preheat", "keep_warm", "stop")
    )


class FryerButton(FryerEntity, ButtonEntity):
    """Refuse invalid commands without claiming the device is offline."""

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = key

    async def async_press(self):
        if self.key == "stop":
            await self.coordinator.stop()
        elif self.key == "preheat":
            await self.coordinator.hold()
        elif self.key == "keep_warm":
            await self.coordinator.keep_warm()
        else:
            await self.coordinator.start()
