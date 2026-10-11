"""Fryer action buttons. Each one goes through the same checks as the services."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FryerConfigEntry, FryerCoordinator
from .entity import FryerEntity

BUTTONS = ("preheat", "start", "stop")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FryerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(FryerButton(entry.runtime_data, name) for name in BUTTONS)


class FryerButton(FryerEntity, ButtonEntity):
    def __init__(self, coordinator: FryerCoordinator, command: str) -> None:
        super().__init__(coordinator, command)
        self._command = command

    async def async_press(self) -> None:
        if self._command == "preheat":
            await self.coordinator.async_command(
                "preheat", self.coordinator.target_temp, self.coordinator.cook_minutes
            )
        else:
            await self.coordinator.async_command(self._command)
