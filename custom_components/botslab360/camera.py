"""Rendered map camera platform for Botslab 360."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from botslab360 import ApiError, AuthenticationError, Device

from . import Botslab360ConfigEntry
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity
from .grid_map import GridMap, decode_grid_map, fetch_map_info, render_grid_png

# Grid map refresh: maximum image age before a background refresh (seconds).
GRID_MAX_AGE_ACTIVE = 60
GRID_MAX_AGE_IDLE = 900
GRID_RETRY_AFTER_FAILURE = 300
GRID_FIRST_WAIT = 8  # HA's camera proxy allows async_camera_image about 10 s
_ACTIVE_STATES = {"sweep", "mop", "backcharge", "pause"}

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a grid-map camera for each robot without room segmentation.

    The polygon room-map camera (Botslab360MapCamera) stays deferred: robots with
    rooms get no camera entity, exactly as before this platform was enabled.
    """

    coordinator = entry.runtime_data.coordinator
    unsegmented = entry.runtime_data.unsegmented_devices
    async_add_entities(
        Botslab360GridMapCamera(entry, coordinator, device)
        for device in coordinator.devices.values()
        if device.id in unsegmented
    )


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
    """Occupancy-grid map with dock and robot position, for unsegmented robots."""

    _attr_content_type = "image/png"
    _attr_should_poll = False
    _attr_translation_key = "map"

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        device: Device,
    ) -> None:
        """Initialize a grid-map camera."""

        Botslab360Entity.__init__(self, coordinator, device)
        Camera.__init__(self)
        self._config_entry = entry
        self._attr_unique_id = f"{device.id}_map"
        self._grid: GridMap | None = None
        self._grid_task: asyncio.Task[None] | None = None
        self._grid_retry_at = 0.0
        self._grid_png: tuple[tuple[float, Any], bytes] | None = None

    async def _async_fetch_grid(self) -> None:
        try:
            info = await fetch_map_info(self.coordinator.client, self.device.id)
            grid = await self.hass.async_add_executor_job(decode_grid_map, info)
        except AuthenticationError:
            self._grid_retry_at = time.monotonic() + GRID_RETRY_AFTER_FAILURE
            self._config_entry.async_start_reauth(self.hass)
            _LOGGER.warning("Authentication failed while fetching the grid map")
        except (ApiError, TimeoutError, OSError) as err:
            self._grid_retry_at = time.monotonic() + GRID_RETRY_AFTER_FAILURE
            _LOGGER.warning("Unable to fetch the grid map: %s", err)
        except Exception:  # noqa: BLE001 - never let a background fetch loop
            self._grid_retry_at = time.monotonic() + GRID_RETRY_AFTER_FAILURE
            _LOGGER.exception("Unexpected error while fetching the grid map")
        else:
            self._grid = grid
            self._grid_png = None
            if self.entity_id is not None:
                self.async_write_ha_state()

    def _start_grid_task(self) -> asyncio.Task[None]:
        # Tied to the config entry so unloading cancels a running fetch.
        return self._config_entry.async_create_background_task(
            self.hass,
            self._async_fetch_grid(),
            f"botslab360 grid map {self.device.id}",
        )

    def _grid_is_stale(self) -> bool:
        if self._grid is None:
            return True
        status = self.robot_status
        state = (
            status.state.casefold()
            if status is not None and isinstance(status.state, str)
            else ""
        )
        max_age = GRID_MAX_AGE_ACTIVE if state in _ACTIVE_STATES else GRID_MAX_AGE_IDLE
        return time.monotonic() - self._grid.fetched > max_age

    async def async_update(self) -> None:
        """Refresh the grid only when explicitly requested by Home Assistant."""

        self._grid_retry_at = 0.0
        if self._grid_task is None or self._grid_task.done():
            self._grid_task = self._start_grid_task()
        await asyncio.shield(self._grid_task)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the cached grid PNG at once; refresh it in the background."""

        if (
            self._grid_is_stale()
            and (self._grid_task is None or self._grid_task.done())
            and time.monotonic() >= self._grid_retry_at
        ):
            self._grid_task = self._start_grid_task()
        if self._grid is None and self._grid_task is not None:
            with suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._grid_task), GRID_FIRST_WAIT)
        grid = self._grid
        if grid is None:
            return None

        status = self.robot_status
        robot = None
        if status is not None:
            x, y = status.position_x, status.position_y
            if (
                isinstance(x, int)
                and isinstance(y, int)
                and not isinstance(x, bool)
                and not isinstance(y, bool)
                and (x, y) != (0, 0)
            ):
                robot = (x, y)
        key = (grid.fetched, robot)
        if self._grid_png is not None and self._grid_png[0] == key:
            return self._grid_png[1]
        try:
            png = await self.hass.async_add_executor_job(render_grid_png, grid, robot)
        except (ValueError, OSError) as err:
            _LOGGER.warning("Unable to render the grid map: %s", err)
            return None
        self._grid_png = (key, png)
        return png
