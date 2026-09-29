"""Persistent Home Assistant preferences for room-cleaning runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from botslab360 import (
    ROOM_CLEAN_TIMES,
    RoomCleaningSettings,
    RoomFanMode,
    RoomWaterLevel,
)

from .areas import DiscoveredRoom
from .const import (
    CONF_CLEAN_TIMES,
    CONF_FAN_MODE,
    CONF_ROOM_PREFERENCES,
    CONF_WATER_PUMP,
    LEGACY_CONF_CLEANING_MODE,
)

FAN_MODE_OPTIONS = tuple(mode.value for mode in RoomFanMode)
CLEAN_TIMES_OPTIONS = tuple(ROOM_CLEAN_TIMES)
WATER_LEVEL_OPTIONS = tuple(level.value for level in RoomWaterLevel)


def room_preference_key(discovered_room: DiscoveredRoom) -> str:
    """Return the stable options key for one robot room."""

    return discovered_room.mapping_key


def room_preference_value(
    options: Mapping[str, Any],
    discovered_room: DiscoveredRoom,
    setting: str,
) -> str | int | None:
    """Return a valid explicit preference or the robot-provided template value."""

    stored = options.get(CONF_ROOM_PREFERENCES, {})
    room_values = stored.get(room_preference_key(discovered_room), {})
    value = room_values.get(setting, getattr(discovered_room.room, setting))
    return value if _is_valid(setting, value) else None


def room_cleaning_settings(
    options: Mapping[str, Any],
    discovered_room: DiscoveredRoom,
) -> RoomCleaningSettings:
    """Build public library settings from HA preferences and robot templates."""

    return RoomCleaningSettings(
        clean_times=room_preference_value(options, discovered_room, CONF_CLEAN_TIMES),
        fan_mode=room_preference_value(options, discovered_room, CONF_FAN_MODE),
        water_pump=room_preference_value(options, discovered_room, CONF_WATER_PUMP),
    )


def update_room_preference(
    options: Mapping[str, Any],
    discovered_room: DiscoveredRoom,
    setting: str,
    value: str | int,
) -> dict[str, Any]:
    """Return config-entry options with one explicit room preference updated."""

    if not _is_valid(setting, value):
        raise ValueError(f"Unsupported room preference: {setting}={value!r}")
    preferences = {
        key: {
            room_setting: room_value
            for room_setting, room_value in room_values.items()
            if room_setting != LEGACY_CONF_CLEANING_MODE
        }
        for key, room_values in options.get(CONF_ROOM_PREFERENCES, {}).items()
    }
    room_values = preferences.setdefault(room_preference_key(discovered_room), {})
    room_values[setting] = value
    return {**options, CONF_ROOM_PREFERENCES: preferences}


def _is_valid(setting: str, value: object) -> bool:
    if setting == CONF_FAN_MODE:
        return isinstance(value, str) and value in FAN_MODE_OPTIONS
    if setting == CONF_CLEAN_TIMES:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value in CLEAN_TIMES_OPTIONS
        )
    if setting == CONF_WATER_PUMP:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value in WATER_LEVEL_OPTIONS
        )
    return False


def remove_legacy_cleaning_mode_preferences(
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove the retired cleaning-mode preference without changing other options."""

    stored = options.get(CONF_ROOM_PREFERENCES)
    if not isinstance(stored, Mapping):
        return dict(options)
    changed = False
    preferences: dict[str, Any] = {}
    for key, room_values in stored.items():
        if not isinstance(room_values, Mapping):
            preferences[key] = room_values
            continue
        cleaned = dict(room_values)
        if LEGACY_CONF_CLEANING_MODE in cleaned:
            cleaned.pop(LEGACY_CONF_CLEANING_MODE)
            changed = True
        preferences[key] = cleaned
    if not changed:
        return dict(options)
    return {**options, CONF_ROOM_PREFERENCES: preferences}
