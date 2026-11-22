"""Occupancy-grid map rendering and refresh for Botslab 360 robots.

The grid is fetched and decoded by ``python-botslab360``
(``client.get_occupancy_grid``). This module only renders it with Pillow and
manages discovery, caching and refresh in Home Assistant (see docs/grid-map.md).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from PIL import Image, ImageDraw

from botslab360 import (
    GRID_CELL_FREE,
    GRID_CELL_OCCUPIED,
    ApiError,
    AuthenticationError,
    Device,
    OccupancyGrid,
    UnsupportedOccupancyGridError,
)

from . import Botslab360ConfigEntry

_LOGGER = logging.getLogger(__name__)

# Orientation. Upstream map.MapTransform.map_point (rooms) mirrors X and keeps
# image-y growing with world-y, i.e. a 180 degree rotation of a north-up view.
# Use the same so both renderers agree (matches the 360 app, checked on a real S6).
MIRROR_X = True
FLIP_Y = False  # False = grid row 0 (y_min) at the top of the image

MAX_SIDE_PX = 1024
MAX_SCALE = 6

GRID_FETCH_TIMEOUT = 30.0
# Backoff after a temporary failure: doubles up to the maximum.
GRID_RETRY_AFTER_FAILURE = 300
GRID_RETRY_MAX = 3600

_UNKNOWN = (40, 44, 52)
_FLOOR = (120, 170, 220)
_WALL = (240, 240, 240)
_DOCK = (60, 200, 90)
_ROBOT = (230, 60, 60)


@dataclass(slots=True)
class GridRender:
    """A fetched grid with its scaled, cached base image."""

    grid: OccupancyGrid
    scale: float  # pixels per cell; below 1 when a large grid is downscaled
    base: Image.Image
    fetched: float = field(default_factory=time.monotonic)


def build_grid_render(grid: OccupancyGrid) -> GridRender:
    """Render the grid cells into a scaled base image (runs in the executor)."""

    image = Image.frombytes("P", (grid.width, grid.height), grid.cells)
    palette = list(_UNKNOWN) * 256
    palette[GRID_CELL_OCCUPIED * 3 : GRID_CELL_OCCUPIED * 3 + 3] = _WALL
    palette[GRID_CELL_FREE * 3 : GRID_CELL_FREE * 3 + 3] = _FLOOR
    image.putpalette(palette)
    image = image.convert("RGB")
    if MIRROR_X:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if FLIP_Y:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    longest = max(grid.width, grid.height)
    scale: float
    size: tuple[int, int]
    if longest <= MAX_SIDE_PX:
        factor = min(MAX_SCALE, MAX_SIDE_PX // longest)  # whole pixels per cell
        scale = factor
        size = (grid.width * factor, grid.height * factor)
    else:
        scale = MAX_SIDE_PX / longest
        size = (
            max(1, min(MAX_SIDE_PX, round(grid.width * scale))),
            max(1, min(MAX_SIDE_PX, round(grid.height * scale))),
        )
    base = image.resize(size, Image.Resampling.NEAREST)
    return GridRender(grid=grid, scale=scale, base=base)


def _to_pixel(render: GridRender, x_mm: float, y_mm: float) -> tuple[float, float]:
    grid = render.grid
    col = (x_mm / 1000 - grid.x_min) / grid.resolution
    row = (y_mm / 1000 - grid.y_min) / grid.resolution  # row 0 = y_min
    if MIRROR_X:
        col = grid.width - col
    if FLIP_Y:
        row = grid.height - row
    # Per-axis factors so rounding of a downscaled image is followed exactly.
    return (
        col * render.base.width / grid.width,
        row * render.base.height / grid.height,
    )


def render_grid_png(render: GridRender, robot: tuple[int, int] | None) -> bytes:
    """Draw the dock and robot on top of the cached base image."""

    image = render.base.copy()
    draw = ImageDraw.Draw(image)
    radius_dock = max(4, int(1.7 * render.scale))
    radius_robot = max(3, int(1.4 * render.scale))
    for point, color, radius in (
        (render.grid.dock_position, _DOCK, radius_dock),
        (robot, _ROBOT, radius_robot),
    ):
        if point is None:
            continue
        cx, cy = _to_pixel(render, *point)
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=color,
            outline=(255, 255, 255),
            width=2,
        )
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Botslab360GridMaps:
    """Per-entry grid discovery, refresh and cache for all robots.

    Discovery runs in the background so optional map support never blocks the
    vacuum and sensor setup. A device gets a camera after its first successful
    fetch; ``UnsupportedOccupancyGridError`` disables the grid for that device
    for the lifetime of the config entry. An authentication failure stops all
    grid requests for that device until the entry is reloaded (after reauth).
    Temporary failures back off and retry.
    At most one fetch runs per device, and unloading cancels fetches and timers.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Botslab360ConfigEntry,
        on_supported: Callable[[Device], None],
    ) -> None:
        """Initialize the manager; call async_start to begin discovery."""

        self._hass = hass
        self._entry = entry
        self._on_supported = on_supported
        self._devices: dict[str, Device] = {}
        self._renders: dict[str, GridRender] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._retry_timers: dict[str, CALLBACK_TYPE] = {}
        self._retry_delay: dict[str, float] = {}
        self._retry_at: dict[str, float] = {}
        self._listeners: dict[str, Callable[[], None]] = {}
        self.unsupported: set[str] = set()
        self.auth_failed: set[str] = set()
        self._unloaded = False
        entry.async_on_unload(self._async_unload)

    @callback
    def async_start(self, devices: list[Device]) -> None:
        """Start background discovery for each robot."""

        for device in devices:
            self._devices[device.id] = device
            self.async_request_refresh(device.id)

    def render(self, device_id: str) -> GridRender | None:
        """Return the last successful grid render for one robot."""

        return self._renders.get(device_id)

    def task(self, device_id: str) -> asyncio.Task[None] | None:
        """Return the running fetch for one robot, if any."""

        task = self._tasks.get(device_id)
        return task if task is not None and not task.done() else None

    @callback
    def async_add_listener(
        self, device_id: str, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Call listener after each successful refresh of one robot's grid."""

        self._listeners[device_id] = listener

        @callback
        def remove() -> None:
            if self._listeners.get(device_id) is listener:
                del self._listeners[device_id]

        return remove

    @callback
    def async_request_refresh(
        self, device_id: str, *, force: bool = False
    ) -> asyncio.Task[None] | None:
        """Start a fetch unless one is running, unsupported or backing off."""

        if (
            self._unloaded
            or device_id in self.unsupported
            or device_id in self.auth_failed
        ):
            return None
        if (running := self.task(device_id)) is not None:
            return running
        if not force and time.monotonic() < self._retry_at.get(device_id, 0.0):
            return None
        self._cancel_retry_timer(device_id)
        task = self._entry.async_create_background_task(
            self._hass,
            self._async_fetch(device_id),
            f"botslab360 occupancy grid {device_id}",
        )
        self._tasks[device_id] = task
        return task

    async def _async_fetch(self, device_id: str) -> None:
        device = self._devices[device_id]
        client = self._entry.runtime_data.client
        try:
            grid = await client.get_occupancy_grid(device, timeout=GRID_FETCH_TIMEOUT)
            render = await self._hass.async_add_executor_job(build_grid_render, grid)
        except UnsupportedOccupancyGridError:
            if device_id in self._renders:
                # The camera exists; keep its last image and try again later.
                _LOGGER.warning("Robot %s map no longer has a grid", device_id)
                self._schedule_retry(device_id)
            else:
                _LOGGER.debug("Robot %s map has no occupancy grid", device_id)
                self.unsupported.add(device_id)
            return
        except AuthenticationError:
            # Reauthentication reloads the entry; do not keep retrying here.
            _LOGGER.warning("Authentication failed while fetching the grid map")
            self.auth_failed.add(device_id)
            self._entry.async_start_reauth(self._hass)
            return
        except (ApiError, TimeoutError, OSError, ValueError) as err:
            _LOGGER.warning("Unable to fetch the grid map: %s", err)
            self._schedule_retry(device_id)
            return
        except Exception:  # optional map support must not stop for good
            _LOGGER.exception("Unexpected error while fetching the grid map")
            self._schedule_retry(device_id)
            return

        first = device_id not in self._renders
        self._renders[device_id] = render
        self._retry_delay.pop(device_id, None)
        self._retry_at.pop(device_id, None)
        if first:
            self._on_supported(device)
        if (listener := self._listeners.get(device_id)) is not None:
            listener()

    def _schedule_retry(self, device_id: str) -> None:
        delay = self._retry_delay.get(device_id, GRID_RETRY_AFTER_FAILURE / 2) * 2
        delay = min(delay, GRID_RETRY_MAX)
        self._retry_delay[device_id] = delay
        self._retry_at[device_id] = time.monotonic() + delay
        if device_id in self._renders or self._unloaded:
            # A camera exists; it asks for refreshes itself after the backoff.
            return

        @callback
        def retry(_now: object) -> None:
            self._retry_timers.pop(device_id, None)
            self.async_request_refresh(device_id, force=True)

        self._cancel_retry_timer(device_id)
        self._retry_timers[device_id] = async_call_later(
            self._hass,
            delay,
            HassJob(retry, "botslab360 occupancy grid retry", cancel_on_shutdown=True),
        )

    def _cancel_retry_timer(self, device_id: str) -> None:
        if (cancel := self._retry_timers.pop(device_id, None)) is not None:
            cancel()

    @callback
    def _async_unload(self) -> None:
        self._unloaded = True
        for cancel in self._retry_timers.values():
            cancel()
        self._retry_timers.clear()
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._listeners.clear()
