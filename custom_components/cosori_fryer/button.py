"""Preheat, Air fry and Stop. Each is unavailable when the fryer can't do it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import FryerStatus
from .coordinator import FryerConfigEntry, FryerCoordinator
from .entity import FryerEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class FryerButtonDescription(ButtonEntityDescription):
    possible: Callable[[FryerStatus], bool]
    press: Callable[[FryerCoordinator], Awaitable[None]]


BUTTONS = (
    FryerButtonDescription(
        key="preheat",
        possible=lambda s: s.can_preheat,
        press=lambda c: c.preheat(c.temperature),
    ),
    FryerButtonDescription(
        key="air_fry",
        possible=lambda s: s.can_air_fry,
        press=lambda c: c.air_fry(c.temperature, c.minutes),
    ),
    FryerButtonDescription(
        key="stop",
        possible=lambda s: s.can_stop,
        press=lambda c: c.stop(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FryerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        FryerButton(entry.runtime_data, description) for description in BUTTONS
    )


class FryerButton(FryerEntity, ButtonEntity):
    entity_description: FryerButtonDescription

    def __init__(
        self, coordinator: FryerCoordinator, description: FryerButtonDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.entity_description.possible(self.coordinator.data)
        )

    async def async_press(self) -> None:
        await self.entity_description.press(self.coordinator)
