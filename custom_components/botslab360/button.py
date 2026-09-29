"""Room-cleaning button platform for Botslab 360."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Botslab360ConfigEntry
from .areas import DiscoveredRoom
from .const import DOMAIN
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity
from .room_preferences import room_cleaning_settings


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one room-cleaning button for every discovered room."""

    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        Botslab360RoomButton(
            entry,
            coordinator,
            prepared.discovered_room,
            prepared.parent_device_id,
        )
        for prepared in entry.runtime_data.rooms
    )


class Botslab360RoomButton(Botslab360Entity, ButtonEntity):
    """Button that starts a cleaning run for one room."""

    _attr_icon = "mdi:broom"
    _attr_translation_key = "clean_room"

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        discovered_room: DiscoveredRoom,
        parent_device_id: str,
    ) -> None:
        """Initialize a room-cleaning button."""

        device = discovered_room.device
        room = discovered_room.room
        super().__init__(coordinator, device)
        self._config_entry = entry
        self._discovered_room = discovered_room
        self.room = room
        self._attr_unique_id = f"{device.id}_room_{room.id}_clean"
        self._attr_device_info = ChildDeviceInfo(
            identifiers={(DOMAIN, f"{device.id}_room_{room.id}")},
            name=room.name or f"Room {room.id}",
            parent_device_id=parent_device_id,
        )
        self._attr_translation_placeholders = {
            "room_name": room.name or f"Room {room.id}"
        }

    async def async_press(self) -> None:
        """Start cleaning this button's room."""

        await self._async_run_command(
            self._config_entry,
            self.coordinator.client.clean_rooms(
                self.device,
                [self.room.id],
                room_settings={
                    self.room.id: room_cleaning_settings(
                        self._config_entry.options,
                        self._discovered_room,
                    )
                },
            ),
        )
