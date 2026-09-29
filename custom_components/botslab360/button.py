"""Room-cleaning button platform for Botslab 360."""

from __future__ import annotations

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from botslab360 import Device

from . import Botslab360ConfigEntry
from .areas import DiscoveredRoom
from .const import DOMAIN
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity
from .room_jobs import active_rooms_by_device, async_remove_stale_job_entities
from .room_preferences import room_cleaning_settings


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one room-cleaning button for every discovered room."""

    coordinator = entry.runtime_data.coordinator
    room_buttons = [
        Botslab360RoomButton(
            entry,
            coordinator,
            prepared.discovered_room,
            prepared.parent_device_id,
        )
        for prepared in entry.runtime_data.rooms
    ]
    rooms_by_device = active_rooms_by_device(entry.runtime_data.rooms)
    job_buttons: list[Botslab360RoomJobButton] = []
    for rooms in rooms_by_device.values():
        device = rooms[0].device
        job_buttons.extend(
            (
                Botslab360RoomJobButton(entry, coordinator, device, start=True),
                Botslab360RoomJobButton(entry, coordinator, device, start=False),
            )
        )
    async_remove_stale_job_entities(
        hass,
        entry.entry_id,
        BUTTON_DOMAIN,
        {entity.unique_id for entity in job_buttons},
    )
    async_add_entities([*room_buttons, *job_buttons])


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


class Botslab360RoomJobButton(Botslab360Entity, ButtonEntity):
    """Start or clear one robot's temporary multi-room cleaning job."""

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        device: Device,
        *,
        start: bool,
    ) -> None:
        """Initialize a central cleaning-job button."""

        super().__init__(coordinator, device)
        self._config_entry = entry
        self._start = start
        action = "start" if start else "clear"
        self._attr_unique_id = f"{device.id}_job_{action}"
        self._attr_translation_key = f"job_{action}"
        self._attr_icon = "mdi:play-circle-outline" if start else "mdi:selection-remove"

    async def async_press(self) -> None:
        """Start the selected job or clear its temporary room selection."""

        job_state = self._config_entry.runtime_data.room_jobs
        if not self._start:
            job_state.async_clear(self.device.id)
            return

        active_rooms = {
            prepared.discovered_room.room.id: prepared.discovered_room
            for prepared in self._config_entry.runtime_data.rooms
            if prepared.discovered_room.device.id == self.device.id
        }
        job_state.async_retain(self.device.id, active_rooms.keys())
        room_ids = job_state.selected_room_ids(self.device.id)
        if not room_ids:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_rooms_selected",
            )
        await self._async_run_command(
            self._config_entry,
            self.coordinator.client.clean_rooms(
                self.device,
                list(room_ids),
                room_settings={
                    room_id: room_cleaning_settings(
                        self._config_entry.options,
                        active_rooms[room_id],
                    )
                    for room_id in room_ids
                },
            ),
        )
        job_state.async_clear(self.device.id)
