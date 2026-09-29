"""Temporary multi-room cleaning job state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .areas import DiscoveredRoom, PreparedRoom
from .const import DOMAIN


class RoomJobState:
    """Keep temporary room selections isolated by physical robot."""

    def __init__(self) -> None:
        """Initialize empty runtime-only selections."""

        self._selected: dict[str, list[int]] = {}
        self._listeners: defaultdict[str, set[Callable[[], None]]] = defaultdict(set)

    def is_selected(self, device_id: str, room_id: int) -> bool:
        """Return whether a room belongs to the robot's current job."""

        return room_id in self._selected.get(device_id, ())

    def selected_room_ids(self, device_id: str) -> tuple[int, ...]:
        """Return selected room IDs in enable order."""

        return tuple(self._selected.get(device_id, ()))

    @callback
    def async_set_selected(
        self,
        device_id: str,
        room_id: int,
        selected: bool,
    ) -> None:
        """Update one room selection without performing I/O."""

        room_ids = self._selected.setdefault(device_id, [])
        if selected:
            if room_id in room_ids:
                return
            room_ids.append(room_id)
        else:
            if room_id not in room_ids:
                return
            room_ids.remove(room_id)
            if not room_ids:
                self._selected.pop(device_id, None)
        self._notify(device_id)

    @callback
    def async_retain(self, device_id: str, active_room_ids: Collection[int]) -> None:
        """Remove selected rooms that are no longer active."""

        selected = self._selected.get(device_id)
        if selected is None:
            return
        retained = [room_id for room_id in selected if room_id in active_room_ids]
        if retained == selected:
            return
        if retained:
            self._selected[device_id] = retained
        else:
            self._selected.pop(device_id, None)
        self._notify(device_id)

    @callback
    def async_clear(self, device_id: str) -> None:
        """Clear one robot's temporary job selection."""

        if self._selected.pop(device_id, None) is not None:
            self._notify(device_id)

    @callback
    def async_listen(
        self,
        device_id: str,
        listener: Callable[[], None],
    ) -> Callable[[], None]:
        """Subscribe an entity to selection changes for one robot."""

        listeners = self._listeners[device_id]
        listeners.add(listener)

        def remove_listener() -> None:
            listeners.discard(listener)
            if not listeners:
                self._listeners.pop(device_id, None)

        return remove_listener

    @callback
    def _notify(self, device_id: str) -> None:
        for listener in tuple(self._listeners.get(device_id, ())):
            listener()


def active_rooms_by_device(
    prepared_rooms: Iterable[PreparedRoom],
) -> dict[str, tuple[DiscoveredRoom, ...]]:
    """Group active rooms by their physical robot."""

    grouped: defaultdict[str, list[DiscoveredRoom]] = defaultdict(list)
    for prepared in prepared_rooms:
        discovered = prepared.discovered_room
        grouped[discovered.device.id].append(discovered)
    return {device_id: tuple(rooms) for device_id, rooms in grouped.items()}


def async_remove_stale_job_entities(
    hass: HomeAssistant,
    config_entry_id: str,
    entity_domain: str,
    expected_unique_ids: Collection[str],
) -> None:
    """Remove stale integration-owned cleaning-job registry entries."""

    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, config_entry_id):
        if (
            registry_entry.domain == entity_domain
            and registry_entry.platform == DOMAIN
            and _is_job_unique_id(registry_entry.unique_id, entity_domain)
            and registry_entry.unique_id not in expected_unique_ids
        ):
            registry.async_remove(registry_entry.entity_id)


def _is_job_unique_id(unique_id: str, entity_domain: str) -> bool:
    if entity_domain == "switch":
        device_id, separator, room_id = unique_id.rpartition("_job_room_")
        return bool(device_id and separator and room_id.isdigit())
    if entity_domain == "button":
        return unique_id.endswith(("_job_start", "_job_clear"))
    return False
