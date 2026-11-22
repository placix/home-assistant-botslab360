"""Rendered map camera platform for Botslab 360."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from botslab360 import ApiError, AuthenticationError, Device

from . import Botslab360ConfigEntry
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity
from .grid_map import Botslab360GridMaps, GridRender, render_grid_png

# Maximum grid age before a background refresh (seconds).
GRID_MAX_AGE_ACTIVE = 60
GRID_MAX_AGE_IDLE = 900
GRID_FIRST_WAIT = 8  # HA's camera proxy allows async_camera_image about 10 s
_ACTIVE_STATES = {"sweep", "mop", "backcharge", "pause"}

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Discover occupancy grids in the background and add a camera per grid.

    A robot gets a camera after its first successful ``get_occupancy_grid()``.
    The polygon room-map camera (Botslab360MapCamera) stays deferred.
    """

    coordinator = entry.runtime_data.coordinator

    @callback
    def add_camera(device: Device) -> None:
        # A fetch can finish while the entry unloads; never add entities then.
        if entry.state not in (
            ConfigEntryState.SETUP_IN_PROGRESS,
            ConfigEntryState.LOADED,
        ):
            return
        async_add_entities([Botslab360GridMapCamera(coordinator, device, grid_maps)])

    grid_maps = Botslab360GridMaps(hass, entry, add_camera)
    grid_maps.async_start(list(coordinator.devices.values()))


class Botslab360MapCamera(Botslab360Entity, Camera):
    """A cached polygon map, not a physical camera or live stream."""

    _attr_content_type = "image/png"
    _attr_should_poll = False
    _attr_translation_key = "map"

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        device: Device,
    ) -> None:
        """Initialize a room-map camera."""

        Botslab360Entity.__init__(self, coordinator, device)
        Camera.__init__(self)
        self._config_entry = entry
        self._map_cache = entry.runtime_data.map_cache
        self._attr_unique_id = f"{device.id}_map"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose calibration and compact card-oriented room metadata."""

        if (rendered := self._map_cache.get(self.device.id)) is None:
            return {
                "calibration_points": [],
                "rooms": [],
                "predefined_selections": [],
            }
        return {
            "calibration_points": list(rendered.calibration_points),
            "rooms": list(rendered.rooms),
            "predefined_selections": list(rendered.predefined_selections),
        }

    async def _async_refresh_map(self) -> None:
        try:
            await self._map_cache.async_refresh(self.device)
        except AuthenticationError:
            self._config_entry.async_start_reauth(self.hass)
            _LOGGER.warning("Authentication failed while refreshing the room map")
        except ApiError as err:
            _LOGGER.warning("Unable to refresh the room map: %s", err)
        else:
            if self.entity_id is not None:
                self.async_write_ha_state()

    async def async_update(self) -> None:
        """Refresh the map only when explicitly requested by Home Assistant."""

        await self._async_refresh_map()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the cached PNG, fetching room data on first use."""

        if self._map_cache.get(self.device.id) is None:
            await self._async_refresh_map()
        rendered = self._map_cache.get(self.device.id)
        return rendered.image if rendered is not None else None


class Botslab360GridMapCamera(Botslab360Entity, Camera):
    """Occupancy-grid map with the dock and the live robot position."""

    _attr_should_poll = False
    _attr_translation_key = "map"

    def __init__(
        self,
        coordinator: Botslab360Coordinator,
        device: Device,
        grid_maps: Botslab360GridMaps,
    ) -> None:
        """Initialize a grid-map camera."""

        Botslab360Entity.__init__(self, coordinator, device)
        Camera.__init__(self)
        # Camera.__init__ resets content_type to JPEG; the images are PNG.
        self.content_type = "image/png"
        self._grid_maps = grid_maps
        self._attr_unique_id = f"{device.id}_occupancy_grid_map"
        self._png: tuple[tuple[float, tuple[int, int] | None], bytes] | None = None

    async def async_added_to_hass(self) -> None:
        """Write the state again after each successful grid refresh."""

        await super().async_added_to_hass()
        self.async_on_remove(
            self._grid_maps.async_add_listener(self.device.id, self._handle_grid)
        )

    @callback
    def _handle_grid(self) -> None:
        self.async_write_ha_state()

    def _is_stale(self, render: GridRender) -> bool:
        status = self.robot_status
        state = (
            status.state.casefold()
            if status is not None and isinstance(status.state, str)
            else ""
        )
        max_age = GRID_MAX_AGE_ACTIVE if state in _ACTIVE_STATES else GRID_MAX_AGE_IDLE
        return time.monotonic() - render.fetched > max_age

    def _robot_position(self) -> tuple[int, int] | None:
        status = self.robot_status
        if status is None:
            return None
        # (0, 0) is what the robot reports before it has a position; the dock
        # can legitimately sit at the origin, the live robot marker cannot.
        x, y = status.position_x, status.position_y
        if (
            isinstance(x, int)
            and isinstance(y, int)
            and not isinstance(x, bool)
            and not isinstance(y, bool)
            and (x, y) != (0, 0)
        ):
            return (x, y)
        return None

    async def async_update(self) -> None:
        """Refresh the grid only when explicitly requested by Home Assistant."""

        task = self._grid_maps.async_request_refresh(self.device.id, force=True)
        if task is not None:
            await asyncio.shield(task)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the cached grid PNG at once; refresh it in the background."""

        render = self._grid_maps.render(self.device.id)
        if render is None or self._is_stale(render):
            self._grid_maps.async_request_refresh(self.device.id)
        if render is None:
            if (task := self._grid_maps.task(self.device.id)) is not None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), GRID_FIRST_WAIT)
            if (render := self._grid_maps.render(self.device.id)) is None:
                return None

        robot = self._robot_position()
        key = (render.fetched, robot)
        if self._png is not None and self._png[0] == key:
            return self._png[1]
        try:
            png = await self.hass.async_add_executor_job(render_grid_png, render, robot)
        except (ValueError, OSError) as err:
            _LOGGER.warning("Unable to render the grid map: %s", err)
            return None
        self._png = (key, png)
        return png
