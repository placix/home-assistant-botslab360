"""Binary sensor platform for Botslab 360."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from botslab360 import Device

from . import Botslab360ConfigEntry
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Botslab 360 binary sensors."""

    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        Botslab360WipingAssemblyBinarySensor(coordinator, device)
        for device in coordinator.devices.values()
    )


class Botslab360WipingAssemblyBinarySensor(Botslab360Entity, BinarySensorEntity):
    """Report whether the robot detects its wiping assembly."""

    _attr_translation_key = "wiping_assembly"
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator: Botslab360Coordinator, device: Device) -> None:
        """Initialize the wiping-assembly sensor."""

        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.id}_wiping_assembly"

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed wiping-assembly presence state."""

        status = self.robot_status
        if status is None or status.mop_status not in (0, 1):
            return None
        return status.mop_status == 1
