"""The temperature and cook time used by the Preheat and Air fry buttons.

These are settings kept in Home Assistant; changing them sends nothing to the fryer.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntityDescription, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MINUTES_MAX, MINUTES_MIN, TEMP_MAX, TEMP_MIN, TEMP_STEP
from .coordinator import FryerConfigEntry, FryerCoordinator
from .entity import FryerEntity

PARALLEL_UPDATES = 0

NUMBERS = (
    NumberEntityDescription(
        key="temperature",
        native_min_value=TEMP_MIN,
        native_max_value=TEMP_MAX,
        native_step=TEMP_STEP,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="minutes",
        native_min_value=MINUTES_MIN,
        native_max_value=MINUTES_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FryerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        FryerSetting(entry.runtime_data, description) for description in NUMBERS
    )


class FryerSetting(FryerEntity, RestoreNumber):
    def __init__(
        self, coordinator: FryerCoordinator, description: NumberEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is None or last.native_value is None:
            return
        value = int(last.native_value)
        description = self.entity_description
        assert (
            description.native_min_value is not None
            and description.native_max_value is not None
        )
        if description.native_min_value <= value <= description.native_max_value:
            setattr(self.coordinator, description.key, value)

    @property
    def native_value(self) -> float:
        return getattr(self.coordinator, self.entity_description.key)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self.coordinator, self.entity_description.key, int(value))
        self.async_write_ha_state()
