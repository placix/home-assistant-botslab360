"""Room-cleaning button platform for Botslab 360."""

from __future__ import annotations

from botslab360 import ApiError, AuthenticationError, Device, Room
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, PlatformNotReady
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Botslab360ConfigEntry
from .const import DOMAIN
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one room-cleaning button for every discovered room."""

    coordinator = entry.runtime_data.coordinator
    entities: list[Botslab360RoomButton] = []
    try:
        for device in coordinator.devices.values():
            rooms = await coordinator.client.get_rooms(device)
            entities.extend(
                Botslab360RoomButton(entry, coordinator, device, room)
                for room in rooms
            )
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
        ) from err
    except ApiError as err:
        raise PlatformNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err

    async_add_entities(entities)


class Botslab360RoomButton(Botslab360Entity, ButtonEntity):
    """Button that starts a cleaning run for one room."""

    _attr_icon = "mdi:broom"
    _attr_translation_key = "clean_room"

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        device: Device,
        room: Room,
    ) -> None:
        """Initialize a room-cleaning button."""

        super().__init__(coordinator, device)
        self._config_entry = entry
        self.room = room
        self._attr_unique_id = f"{device.id}_room_{room.id}_clean"
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
            ),
        )
