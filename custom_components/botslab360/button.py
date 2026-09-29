"""Room-cleaning button platform for Botslab 360."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, PlatformNotReady
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from botslab360 import ApiError, AuthenticationError, Device, Room

from . import Botslab360ConfigEntry
from .areas import DiscoveredRoom, room_area_fields
from .const import CONF_ROOM_AREAS, DOMAIN
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity, async_get_or_create_robot_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one room-cleaning button for every discovered room."""

    coordinator = entry.runtime_data.coordinator
    entities: list[Botslab360RoomButton] = []
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)
    configured_mappings = entry.options.get(CONF_ROOM_AREAS, {})
    try:
        for device in coordinator.devices.values():
            rooms = await coordinator.client.get_rooms(device)
            parent = async_get_or_create_robot_device(
                device_registry,
                entry.entry_id,
                device,
            )
            fields = room_area_fields(
                (DiscoveredRoom(device, room) for room in rooms),
                area_registry.async_list_areas(),
                configured_mappings,
            )
            for field in fields:
                room = field.discovered_room.room
                child = device_registry.async_get_or_create_child(
                    config_entry_id=entry.entry_id,
                    identifiers={(DOMAIN, f"{device.id}_room_{room.id}")},
                    name=room.name or f"Room {room.id}",
                    parent_device_id=parent.id,
                )
                # None removes the child's explicit Area. Home Assistant may then
                # expose the parent's Area as the child's effective Area.
                if child.area_id != field.area_id:
                    child = device_registry.async_update_child_device(
                        child.id,
                        area_id=field.area_id,
                    )
                entities.append(
                    Botslab360RoomButton(
                        entry,
                        coordinator,
                        device,
                        room,
                        child.parent_device_id,
                    )
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
        parent_device_id: str,
    ) -> None:
        """Initialize a room-cleaning button."""

        super().__init__(coordinator, device)
        self._config_entry = entry
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
            ),
        )
