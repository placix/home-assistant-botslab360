"""Room-to-area assignment helpers for Botslab 360."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from homeassistant.helpers.area_registry import AreaEntry

from botslab360 import Device, Room


@dataclass(frozen=True, slots=True)
class DiscoveredRoom:
    """A room together with the robot that owns it."""

    device: Device
    room: Room

    @property
    def mapping_key(self) -> str:
        """Return the stable options key for this robot room."""

        return f"{self.device.id}:{self.room.id}"


@dataclass(frozen=True, slots=True)
class RoomAreaField:
    """A room field displayed in a config or options flow."""

    discovered_room: DiscoveredRoom
    label: str
    area_id: str | None


def normalize_area_name(name: str) -> str:
    """Normalize a room or area name for conservative exact matching."""

    return " ".join(name.split()).casefold()


def automatic_room_area_matches(
    rooms: Iterable[DiscoveredRoom],
    areas: Iterable[AreaEntry],
) -> dict[str, str]:
    """Return unambiguous exact normalized room-to-area matches."""

    area_ids_by_name: defaultdict[str, list[str]] = defaultdict(list)
    for area in areas:
        area_ids_by_name[normalize_area_name(area.name)].append(area.id)

    matches: dict[str, str] = {}
    for discovered_room in rooms:
        area_ids = area_ids_by_name[normalize_area_name(discovered_room.room.name)]
        if len(area_ids) == 1:
            matches[discovered_room.mapping_key] = area_ids[0]
    return matches


def room_area_fields(
    rooms: Iterable[DiscoveredRoom],
    areas: Iterable[AreaEntry],
    explicit_mappings: Mapping[str, str | None],
) -> list[RoomAreaField]:
    """Build uniquely labelled room fields with explicit or automatic defaults."""

    room_list = list(rooms)
    area_list = list(areas)
    automatic_matches = automatic_room_area_matches(room_list, area_list)
    valid_area_ids = {area.id for area in area_list}
    multiple_robots = len({room.device.id for room in room_list}) > 1

    base_labels = [
        _room_label(room, include_robot=multiple_robots) for room in room_list
    ]
    label_counts = Counter(base_labels)
    fields: list[RoomAreaField] = []
    for discovered_room, base_label in zip(room_list, base_labels, strict=True):
        mapping_key = discovered_room.mapping_key
        if mapping_key in explicit_mappings:
            area_id = explicit_mappings[mapping_key]
        else:
            area_id = automatic_matches.get(mapping_key)
        if area_id not in valid_area_ids:
            area_id = None

        label = base_label
        if label_counts[base_label] > 1:
            label = (
                f"{base_label} [{discovered_room.device.id}:{discovered_room.room.id}]"
            )
        fields.append(RoomAreaField(discovered_room, label, area_id))
    return fields


def _room_label(room: DiscoveredRoom, *, include_robot: bool) -> str:
    room_name = room.room.name or f"Room {room.room.id}"
    if not include_robot:
        return room_name
    robot_name = room.device.name or room.device.id
    return f"{robot_name}: {room_name}"
