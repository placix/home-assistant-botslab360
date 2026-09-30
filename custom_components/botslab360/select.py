"""Cleaning-mode and per-room preference selects for Botslab 360."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from botslab360 import Device

from . import Botslab360ConfigEntry
from .areas import DiscoveredRoom
from .const import (
    CONF_CLEAN_TIMES,
    CONF_FAN_MODE,
    CONF_WATER_PUMP,
    DOMAIN,
    LEGACY_CONF_CLEANING_MODE,
)
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity
from .room_preferences import (
    CLEAN_TIMES_OPTIONS,
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
    RoomSelectDescription(CONF_FAN_MODE, FAN_MODE_OPTIONS),
    RoomSelectDescription(CONF_CLEAN_TIMES, CLEAN_TIMES_OPTIONS),
    RoomSelectDescription(CONF_WATER_PUMP, WATER_LEVEL_OPTIONS),
)

CLEANING_MODE_SWEEP = "sweep"
CLEANING_MODE_SWEEP_AND_MOP = "sweep_and_mop"
CLEANING_MODE_MOP = "mop"
CLEANING_MODE_OPTIONS = (
    CLEANING_MODE_SWEEP,
    CLEANING_MODE_SWEEP_AND_MOP,
    CLEANING_MODE_MOP,
)


def _is_legacy_cleaning_mode_unique_id(unique_id: str) -> bool:
    """Return whether an entity ID belongs to the retired room mode select."""

    _device_id, separator, room_setting = unique_id.rpartition("_room_")
    if not separator:
        return False
    room_id, separator, setting = room_setting.rpartition("_")
    return (
        bool(separator) and room_id.isdigit() and setting == LEGACY_CONF_CLEANING_MODE
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up cleaning preference selects for every active room."""

    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registry_entry.domain == "select"
            and registry_entry.platform == DOMAIN
            and _is_legacy_cleaning_mode_unique_id(registry_entry.unique_id)
        ):
            registry.async_remove(registry_entry.entity_id)

    coordinator = entry.runtime_data.coordinator
    entities: list[Botslab360RobotCleaningModeSelect | Botslab360RoomSelect] = [
        Botslab360RobotCleaningModeSelect(entry, coordinator, device)
        for device in coordinator.devices.values()
    ]
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


class Botslab360RobotCleaningModeSelect(Botslab360Entity, SelectEntity):
    """Robot cleaning mode backed by wiping hardware and the mop-only switch."""

    _attr_translation_key = "cleaning_mode"
    _attr_icon = "mdi:robot-vacuum"
    _attr_options: ClassVar[list[str]] = list(CLEANING_MODE_OPTIONS)

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        device: Device,
    ) -> None:
        """Initialize the robot cleaning-mode select."""

        super().__init__(coordinator, device)
        self._config_entry = entry
        self._attr_unique_id = f"{device.id}_cleaning_mode"
        self._optimistic_mop_only: bool | None = None
        self._observed_mop_status = self._mop_status

    @property
    def _mop_status(self) -> int | None:
        """Return the raw wiping-hardware status."""

        status = self.robot_status
        return status.mop_status if status is not None else None

    @property
    def current_option(self) -> str | None:
        """Return the known effective cleaning mode."""

        mop_status = self._mop_status
        if mop_status != self._observed_mop_status:
            self._optimistic_mop_only = None
            self._observed_mop_status = mop_status

        if mop_status == 0:
            return CLEANING_MODE_SWEEP
        if mop_status != 1 or self._optimistic_mop_only is None:
            return None
        return (
            CLEANING_MODE_MOP
            if self._optimistic_mop_only
            else CLEANING_MODE_SWEEP_AND_MOP
        )

    async def async_select_option(self, option: str) -> None:
        """Set a cleaning mode that is valid for the installed hardware."""

        if option not in self.options:
            raise ValueError(f"Unsupported option: {option}")

        mop_status = self._mop_status
        if mop_status not in (0, 1):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mop_status_unknown",
            )
        if option == CLEANING_MODE_SWEEP:
            if mop_status != 0:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="remove_wiping_assembly",
                )
            mop_only = False
        else:
            if mop_status != 1:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="wiping_assembly_required",
                )
            mop_only = option == CLEANING_MODE_MOP

        await self._async_run_command(
            self._config_entry,
            self.coordinator.client.set_mop_only(self.device, mop_only),
        )
        self._optimistic_mop_only = mop_only
        self._observed_mop_status = mop_status
        self.async_write_ha_state()


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
