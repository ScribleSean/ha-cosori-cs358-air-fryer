"""Shared base for the fryer's entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DEVICE_TYPE
from .const import DOMAIN
from .coordinator import FryerCoordinator


class FryerEntity(CoordinatorEntity[FryerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FryerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        device = coordinator.client.device
        assert device is not None
        self._attr_translation_key = key
        self._attr_unique_id = f"{device.cid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.cid)},
            name=device.name,
            manufacturer="COSORI",
            model=DEVICE_TYPE,
        )
