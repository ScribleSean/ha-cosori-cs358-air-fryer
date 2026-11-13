"""Common coordinator entity metadata."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FryerCoordinator


class FryerEntity(CoordinatorEntity[FryerCoordinator]):
    """An entity belonging to the discovered fryer."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FryerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_translation_key = key
        self._attr_unique_id = f"{device.cid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.cid)},
            name=device.device_name,
            manufacturer="Cosori",
            model="CS358-AF",
            suggested_area="Kitchen",
        )
