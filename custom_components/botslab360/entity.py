"""Base entities for Botslab 360."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from botslab360 import ApiError, AuthenticationError, Device, RobotStatus

from .const import DOMAIN, MANUFACTURER
from .coordinator import Botslab360Coordinator


class Botslab360Entity(CoordinatorEntity[Botslab360Coordinator]):
    """Base class for a Botslab 360 entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Botslab360Coordinator, device: Device) -> None:
        """Initialize the entity."""

        super().__init__(coordinator)
        self.device = device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            manufacturer=MANUFACTURER,
            model=device.model or None,
            name=device.name or f"Botslab 360 {device.id}",
        )

    @property
    def robot_status(self) -> RobotStatus | None:
        """Return this robot's most recently polled status."""

        return self.coordinator.data.get(self.device.id)

    async def _async_run_command(
        self,
        entry: ConfigEntry,
        command: Awaitable[None],
    ) -> None:
        """Run a library command and refresh status after success."""

        try:
            await command
        except AuthenticationError as err:
            entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except ApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
            ) from err

        await self.coordinator.async_request_refresh()
