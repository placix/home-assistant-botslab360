"""Room-to-area assignment helpers for Botslab 360."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.area_registry import AreaEntry

from botslab360 import Device, Room

from .const import CONF_IGNORED_ROOMS, CONF_ROOM_AREAS, DOMAIN
from .entity import async_get_or_create_robot_device


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
    ignored: bool

    @property
    def ignore_label(self) -> str:
        """Return the transient form label for this room's ignore toggle."""

        return f"{self.label} - Ignore room / Raum ignorieren"


@dataclass(frozen=True, slots=True)
class PreparedRoom:
    """A non-ignored room with its registered parent robot device ID."""

    discovered_room: DiscoveredRoom
    parent_device_id: str


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
    ignored_rooms: Collection[str] = (),
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
        fields.append(
            RoomAreaField(
                discovered_room,
                label,
                area_id,
                mapping_key in ignored_rooms,
            )
        )
    return fields


def async_prepare_room_devices(
    hass: HomeAssistant,
    config_entry_id: str,
    options: Mapping[str, object],
    rooms: Iterable[DiscoveredRoom],
) -> tuple[PreparedRoom, ...]:
    """Create active room child devices and remove ignored room registry data."""

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    ignored_rooms = set(options.get(CONF_IGNORED_ROOMS, ()))
    fields = room_area_fields(
        rooms,
        ar.async_get(hass).async_list_areas(),
        options.get(CONF_ROOM_AREAS, {}),
        ignored_rooms,
    )
    prepared: list[PreparedRoom] = []

    for field in fields:
        discovered = field.discovered_room
        room_identifier = (
            DOMAIN,
            f"{discovered.device.id}_room_{discovered.room.id}",
        )
        if field.ignored:
            child = device_registry.async_get_child_device_by_identifier(
                room_identifier,
                config_entry_id,
            )
            if child is not None:
                for entity in er.async_entries_for_device(
                    entity_registry,
                    child.id,
                    include_disabled_entities=True,
                ):
                    if (
                        entity.config_entry_id == config_entry_id
                        and entity.platform == DOMAIN
                    ):
                        entity_registry.async_remove(entity.entity_id)
                device_registry.async_remove_device(child.id)
            continue

        parent = async_get_or_create_robot_device(
            device_registry,
            config_entry_id,
            discovered.device,
        )
        child = device_registry.async_get_or_create_child(
            config_entry_id=config_entry_id,
            identifiers={room_identifier},
            name=discovered.room.name or f"Room {discovered.room.id}",
            parent_device_id=parent.id,
        )
        if child.area_id != field.area_id:
            child = device_registry.async_update_child_device(
                child.id,
                area_id=field.area_id,
            )
        prepared.append(PreparedRoom(discovered, child.parent_device_id))

    return tuple(prepared)


def _room_label(room: DiscoveredRoom, *, include_robot: bool) -> str:
    room_name = room.room.name or f"Room {room.room.id}"
    if not include_robot:
        return room_name
    robot_name = room.device.name or room.device.id
    return f"{robot_name}: {room_name}"
