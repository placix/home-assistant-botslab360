"""Temporary room-selection switches for multi-room cleaning jobs."""

from __future__ import annotations

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Botslab360ConfigEntry
from .areas import DiscoveredRoom
from .const import DOMAIN
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity
from .room_jobs import async_remove_stale_job_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one temporary job switch for every active room."""

    coordinator = entry.runtime_data.coordinator
    entities = [
        Botslab360RoomJobSwitch(
            entry,
            coordinator,
            prepared.discovered_room,
            prepared.parent_device_id,
        )
        for prepared in entry.runtime_data.rooms
    ]
    async_remove_stale_job_entities(
        hass,
        entry.entry_id,
        SWITCH_DOMAIN,
        {entity.unique_id for entity in entities},
    )
    async_add_entities(entities)


class Botslab360RoomJobSwitch(Botslab360Entity, SwitchEntity):
    """Select one room for the robot's temporary cleaning job."""

    _attr_icon = "mdi:checkbox-marked-circle-outline"
    _attr_translation_key = "job_room"

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        discovered_room: DiscoveredRoom,
        parent_device_id: str,
    ) -> None:
        """Initialize a room job-selection switch."""

        device = discovered_room.device
        room = discovered_room.room
        super().__init__(coordinator, device)
        self._job_state = entry.runtime_data.room_jobs
        self._room_id = room.id
        self._attr_unique_id = f"{device.id}_job_room_{room.id}"
        self._attr_device_info = ChildDeviceInfo(
            identifiers={(DOMAIN, f"{device.id}_room_{room.id}")},
            name=room.name or f"Room {room.id}",
            parent_device_id=parent_device_id,
        )
        self._attr_translation_placeholders = {
            "room_name": room.name or f"Room {room.id}"
        }

    @property
    def is_on(self) -> bool:
        """Return whether this room belongs to the temporary job."""

        return self._job_state.is_selected(self.device.id, self._room_id)

    async def async_added_to_hass(self) -> None:
        """Subscribe to shared selection-state updates."""

        await super().async_added_to_hass()
        self.async_on_remove(
            self._job_state.async_listen(
                self.device.id,
                self._async_handle_selection_update,
            )
        )

    async def async_turn_on(self, **kwargs: object) -> None:
        """Add this room to the temporary job without robot I/O."""

        self._job_state.async_set_selected(self.device.id, self._room_id, True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Remove this room from the temporary job without robot I/O."""

        self._job_state.async_set_selected(self.device.id, self._room_id, False)

    @callback
    def _async_handle_selection_update(self) -> None:
        self.async_write_ha_state()
