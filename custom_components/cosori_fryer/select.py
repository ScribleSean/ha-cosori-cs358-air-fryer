"""The fryer's programs, which fill in a temperature and time."""

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import PRESETS
from .entity import FryerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FryerPreset(entry.runtime_data)])


class FryerPreset(FryerEntity, SelectEntity, RestoreEntity):
    """Choosing a program sends nothing; Start and Preheat use it."""

    _attr_options = PRESETS

    def __init__(self, coordinator):
        super().__init__(coordinator, "preset")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in PRESETS:
            # Restore only the choice; the restored temperature and time stay.
            self.coordinator.update_settings(preset=last.state)

    @property
    def current_option(self) -> str:
        return self.coordinator.settings.preset

    async def async_select_option(self, option: str) -> None:
        self.coordinator.choose_preset(option)
