"""Polling and command handling for the fryer."""

from __future__ import annotations

import logging
import time
from typing import Any

from pyvesync.utils.errors import VeSyncError

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTIVE_LINGER_SECONDS,
    CONF_ALLOW_UNVERIFIED,
    CONF_NOTIFY_SERVICE,
    DEFAULT_MINUTES,
    DEFAULT_TEMP_F,
    DOMAIN,
    EVENT_COOK_DONE,
    EVENT_PREHEAT_DONE,
    POLL_ACTIVE,
    POLL_IDLE,
)
from .cosori358 import (
    COMMANDS,
    COOK_PAUSED,
    COOKING,
    IDLE,
    PREHEAT_DONE,
    PREHEAT_PAUSED,
    PREHEATING,
    Cosori358,
    FryerError,
    FryerStatus,
    Refused,
    plan,
)

_LOGGER = logging.getLogger(__name__)

# States worth watching closely. Plain "basket out" while idle is not one.
_BUSY = {PREHEATING, PREHEAT_PAUSED, PREHEAT_DONE, COOKING, COOK_PAUSED}

type FryerConfigEntry = ConfigEntry[FryerCoordinator]


class FryerCoordinator(DataUpdateCoordinator[FryerStatus]):
    """Reads the fryer every 30 s while busy and every 5 min when idle."""

    config_entry: FryerConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: FryerConfigEntry, manager: Any, fryer: Cosori358
    ) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_IDLE
        )
        self.manager = manager
        self.fryer = fryer
        self.target_temp: int = DEFAULT_TEMP_F
        self.cook_minutes: int = DEFAULT_MINUTES
        self._busy_until = 0.0

    async def _async_update_data(self) -> FryerStatus:
        try:
            status = await self.fryer.status()
        except (FryerError, VeSyncError) as err:
            raise UpdateFailed(str(err)) from err

        if self.data is not None:
            await self._notify_transitions(self.data.cook_status, status)
        if status.cook_status in _BUSY:
            self._busy_until = time.monotonic() + ACTIVE_LINGER_SECONDS
        busy = time.monotonic() < self._busy_until
        self.update_interval = POLL_ACTIVE if busy else POLL_IDLE
        return status

    async def _notify_transitions(self, before: str, status: FryerStatus) -> None:
        after = status.cook_status
        unit = f"°{status.temp_unit.upper()}"
        if before == PREHEATING and after == PREHEAT_DONE:
            event, title = EVENT_PREHEAT_DONE, "Air fryer preheated"
            message = f"Preheat finished at {status.preheat_temp}{unit}. Add food and start cooking."
        elif before == COOKING and after in IDLE:
            event, title = EVENT_COOK_DONE, "Air fryer done"
            message = "Cooking has finished."
        else:
            return

        self.hass.bus.async_fire(event, {"status": after})
        persistent_notification.async_create(
            self.hass, message, title=title, notification_id=event
        )
        service = self.config_entry.options.get(CONF_NOTIFY_SERVICE)
        if service and self.hass.services.has_service("notify", service):
            await self.hass.services.async_call(
                "notify", service, {"title": title, "message": message}, blocking=False
            )

    async def async_command(
        self, name: str, temp: int | None = None, minutes: int | None = None
    ) -> None:
        """Run a command with the same checks as the CLI."""
        try:
            status = await self.fryer.status()
            method, data = plan(name, status, self.manager.account_id, temp, minutes)
        except Refused as err:
            raise ServiceValidationError(str(err)) from err
        except (FryerError, VeSyncError) as err:
            raise HomeAssistantError(str(err)) from err

        allowed = self.config_entry.options.get(CONF_ALLOW_UNVERIFIED, False)
        if not COMMANDS[name].verified and not allowed:
            raise ServiceValidationError(
                f"'{name}' has not been proven on the real fryer yet. Turn on "
                "'Allow commands not yet proven' in the integration's options to send it."
            )

        try:
            await self.fryer.send(method, data)
        except (FryerError, VeSyncError) as err:
            raise HomeAssistantError(str(err)) from err

        self._busy_until = time.monotonic() + ACTIVE_LINGER_SECONDS
        await self.async_refresh()
