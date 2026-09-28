"""Base entities for Botslab 360."""

from __future__ import annotations

from botslab360 import Device, RobotStatus
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import Botslab360Coordinator


class Botslab360Entity(CoordinatorEntity[Botslab360Coordinator]):
    """Base class for a Botslab 360 entity."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: Botslab360Coordinator, device: Device
    ) -> None:
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
