"""Finish events, emitted once per observed cooking phase."""

from typing import ClassVar

from homeassistant.components.event import EventEntity
from homeassistant.core import callback

from .entity import FryerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FryerFinishEvent(entry.runtime_data)])


class FryerFinishEvent(FryerEntity, EventEntity):
    """A single event entity for both finish types."""

    _attr_event_types: ClassVar[list[str]] = ["preheat_finished", "cook_finished"]

    def __init__(self, coordinator):
        super().__init__(coordinator, "finished")
        self._sequence = coordinator.data.finish_sequence

    @callback
    def _handle_coordinator_update(self):
        data = self.coordinator.data
        if data.finish_sequence != self._sequence:
            self._sequence = data.finish_sequence
            self._trigger_event(data.finish)
        super()._handle_coordinator_update()
