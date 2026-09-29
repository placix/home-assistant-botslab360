"""Per-room cleaning preference selects for Botslab 360."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Botslab360ConfigEntry
from .areas import DiscoveredRoom
from .const import (
    CONF_CLEAN_TIMES,
    CONF_CLEANING_MODE,
    CONF_FAN_MODE,
    CONF_WATER_PUMP,
    DOMAIN,
)
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity
from .room_preferences import (
    CLEAN_TIMES_OPTIONS,
    CLEANING_MODE_OPTIONS,
    FAN_MODE_OPTIONS,
    WATER_LEVEL_OPTIONS,
    room_preference_value,
    update_room_preference,
)


@dataclass(frozen=True, slots=True)
class RoomSelectDescription:
    """Description of one room preference select."""

    key: str
    options: tuple[str | int, ...]


ROOM_SELECTS = (
    RoomSelectDescription(CONF_CLEANING_MODE, CLEANING_MODE_OPTIONS),
    RoomSelectDescription(CONF_FAN_MODE, FAN_MODE_OPTIONS),
    RoomSelectDescription(CONF_CLEAN_TIMES, CLEAN_TIMES_OPTIONS),
    RoomSelectDescription(CONF_WATER_PUMP, WATER_LEVEL_OPTIONS),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up cleaning preference selects for every active room."""

    coordinator = entry.runtime_data.coordinator
    entities: list[Botslab360RoomSelect] = []
    for prepared in entry.runtime_data.rooms:
        discovered = prepared.discovered_room
        for description in ROOM_SELECTS:
            current = room_preference_value(
                entry.options,
                discovered,
                description.key,
            )
            if description.key == CONF_WATER_PUMP and current is None:
                continue
            entities.append(
                Botslab360RoomSelect(
                    entry,
                    coordinator,
                    discovered,
                    prepared.parent_device_id,
                    description,
                    current,
                )
            )
    async_add_entities(entities)


class Botslab360RoomSelect(Botslab360Entity, SelectEntity):
    """Native select for one preferred room-cleaning setting."""

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        discovered_room: DiscoveredRoom,
        parent_device_id: str,
        description: RoomSelectDescription,
        current: str | int | None,
    ) -> None:
        """Initialize a room preference select."""

        device = discovered_room.device
        room = discovered_room.room
        super().__init__(coordinator, device)
        self._config_entry = entry
        self._discovered_room = discovered_room
        self._description = description
        self._attr_translation_key = f"room_{description.key}"
        self._attr_unique_id = f"{device.id}_room_{room.id}_{description.key}"
        self._attr_device_info = ChildDeviceInfo(
            identifiers={(DOMAIN, f"{device.id}_room_{room.id}")},
            name=room.name or f"Room {room.id}",
            parent_device_id=parent_device_id,
        )
        self._attr_options = [str(option) for option in description.options]
        self._attr_current_option = None if current is None else str(current)

    async def async_select_option(self, option: str) -> None:
        """Persist one HA-side room preference."""

        if option not in self.options:
            raise ValueError(f"Unsupported option: {option}")
        value: Any = option if self._description.key == CONF_FAN_MODE else int(option)
        new_options = update_room_preference(
            self._config_entry.options,
            self._discovered_room,
            self._description.key,
            value,
        )
        self.hass.config_entries.async_update_entry(
            self._config_entry,
            options=new_options,
        )
        self._attr_current_option = option
        self.async_write_ha_state()
