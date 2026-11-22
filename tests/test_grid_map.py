"""Tests for the occupancy-grid map camera."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import pytest
from botslab360 import (
    GRID_CELL_FREE,
    GRID_CELL_OCCUPIED,
    GRID_CELL_UNKNOWN,
    ApiError,
    AuthenticationError,
    OccupancyGrid,
    UnsupportedOccupancyGridError,
)
from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from PIL import Image
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.botslab360.const import DOMAIN
from custom_components.botslab360.grid_map import (
    GRID_RETRY_AFTER_FAILURE,
    MAX_SIDE_PX,
    _to_pixel,
    build_grid_render,
    render_grid_png,
)

from .conftest import TEST_CREDENTIALS, TEST_DEVICE, TEST_ROOMS, make_status

WIDTH, HEIGHT = 8, 6
ROBOT = (230, 60, 60)
DOCK = (60, 200, 90)
WALL = (240, 240, 240)
FLOOR = (120, 170, 220)
UNIQUE_ID = f"{TEST_DEVICE.id}_occupancy_grid_map"


def _cells() -> bytes:
    """Unknown border, floor inside, one wall cell."""

    cells = bytearray([GRID_CELL_UNKNOWN] * (WIDTH * HEIGHT))
    for row in range(1, HEIGHT - 1):
        for col in range(1, WIDTH - 1):
            cells[row * WIDTH + col] = GRID_CELL_FREE
    cells[4 * WIDTH + 1] = GRID_CELL_OCCUPIED  # away from the dock marker
    return bytes(cells)


def _grid(**changes) -> OccupancyGrid:
    grid = OccupancyGrid(
        map_id=1700000000,
        width=WIDTH,
        height=HEIGHT,
        resolution=0.05,
        x_min=-0.2,
        y_min=-0.1,
        cells=_cells(),
        dock_position=(0, 0),
    )
    return replace(grid, **changes)


def _colours(png: bytes) -> set[tuple[int, int, int]]:
    with Image.open(BytesIO(png)) as image:
        return {colour for _, colour in image.convert("RGB").getcolors(1_000_000)}


async def _setup(hass, mock_client) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _camera_id(hass, unique_id: str = UNIQUE_ID) -> str | None:
    return er.async_get(hass).async_get_entity_id("camera", DOMAIN, unique_id)


def test_render_draws_grid_dock_and_robot() -> None:
    """The PNG has the scaled size, the cell colours and both markers."""

    render = build_grid_render(_grid())
    png = render_grid_png(render, (100, 100))

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(png)) as image:
        assert image.size == (WIDTH * render.scale, HEIGHT * render.scale)
    colours = _colours(png)
    assert {ROBOT, DOCK, (240, 240, 240), (120, 170, 220), (40, 44, 52)} <= colours


def test_render_without_dock_or_robot() -> None:
    """A grid without a dock position renders without markers."""

    png = render_grid_png(build_grid_render(_grid(dock_position=None)), None)

    assert not {ROBOT, DOCK} & _colours(png)


def test_pixel_mapping_mirrors_x_and_keeps_row_zero_at_y_min() -> None:
    """The S6-verified orientation: X mirrored, row 0 at y_min."""

    render = build_grid_render(_grid())
    scale = render.scale

    assert _to_pixel(render, -200, -100) == (WIDTH * scale, 0)
    assert _to_pixel(render, 200, 200) == pytest.approx((0, HEIGHT * scale))


@pytest.mark.parametrize(
    ("width", "height", "size"),
    [
        (2048, 1, (MAX_SIDE_PX, 1)),
        (1, 2048, (1, MAX_SIDE_PX)),
        (3000, 1500, (MAX_SIDE_PX, 512)),
        (1500, 3000, (512, MAX_SIDE_PX)),
        (MAX_SIDE_PX, 1, (MAX_SIDE_PX, 1)),
    ],
)
def test_large_grid_is_downscaled_to_max_side(width, height, size) -> None:
    """A grid longer than MAX_SIDE_PX cells is scaled below one pixel per cell."""

    resolution = 0.05
    # A wall block around one cell, large enough to survive downscaling.
    col, row = width // 3, height // 3
    cells = bytearray([GRID_CELL_FREE]) * (width * height)
    for r in range(max(0, row - 4), min(height, row + 5)):
        for c in range(max(0, col - 4), min(width, col + 5)):
            cells[r * width + c] = GRID_CELL_OCCUPIED
    grid = OccupancyGrid(
        map_id=1,
        width=width,
        height=height,
        resolution=resolution,
        x_min=-1.0,
        y_min=-2.0,
        cells=bytes(cells),
        dock_position=(0, 0),
    )
    render = build_grid_render(grid)

    assert render.base.size == size
    with Image.open(BytesIO(render_grid_png(render, (500, 500)))) as image:
        assert image.size == size

    # Same S6 orientation: the grid corners map to the image corners.
    x_min, y_min = grid.x_min * 1000, grid.y_min * 1000
    x_max = x_min + width * resolution * 1000
    y_max = y_min + height * resolution * 1000
    assert _to_pixel(render, x_min, y_min) == pytest.approx((size[0], 0))
    assert _to_pixel(render, x_max, y_max) == pytest.approx((0, size[1]))
    # The centre of the wall cell lands on a wall pixel of the resized image.
    px, py = _to_pixel(
        render,
        x_min + (col + 0.5) * resolution * 1000,
        y_min + (row + 0.5) * resolution * 1000,
    )
    assert render.base.getpixel((int(px), int(py))) == WALL
    # And the opposite corner area stays floor.
    fx, fy = _to_pixel(render, x_max - 0.5 * resolution * 1000, y_max - 1)
    assert render.base.getpixel((int(fx), min(int(fy), size[1] - 1))) == FLOOR


async def test_camera_created_only_for_robots_with_a_grid(hass, mock_client) -> None:
    """get_occupancy_grid() decides the camera, independent of rooms."""

    other = replace(TEST_DEVICE, id="no-grid-device", name="No Grid Robot")
    mock_client.get_devices.return_value = [TEST_DEVICE, other]
    # The robot with rooms has a grid; the one without rooms has none.
    mock_client.get_rooms.side_effect = lambda device: (
        TEST_ROOMS if device.id == TEST_DEVICE.id else []
    )
    mock_client.get_status.side_effect = lambda device_id: replace(
        make_status(), device_id=device_id
    )

    async def occupancy_grid(device, timeout):
        if device.id == TEST_DEVICE.id:
            return _grid()
        raise UnsupportedOccupancyGridError("no grid", phase="unsupported")

    mock_client.get_occupancy_grid.side_effect = occupancy_grid
    await _setup(hass, mock_client)

    camera_id = _camera_id(hass)
    assert camera_id is not None
    assert _camera_id(hass, f"{other.id}_occupancy_grid_map") is None
    assert _camera_id(hass, f"{TEST_DEVICE.id}_map") is None

    image = await async_get_image(hass, camera_id)
    assert image.content_type == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert mock_client.get_occupancy_grid.await_count == 2


async def test_unsupported_grid_is_not_retried(hass, mock_client) -> None:
    """UnsupportedOccupancyGridError disables the grid for the entry lifetime."""

    await _setup(hass, mock_client)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=3))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _camera_id(hass) is None
    mock_client.get_occupancy_grid.assert_awaited_once()


async def test_slow_grid_discovery_does_not_block_setup(hass, mock_client) -> None:
    """Setup finishes while discovery waits; unloading cancels the fetch."""

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(device, timeout):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    mock_client.get_occupancy_grid.side_effect = hang
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await asyncio.wait_for(started.wait(), 1)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("vacuum.test_robot") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert cancelled.is_set()


@pytest.mark.parametrize(
    "error",
    [
        ApiError("Timed out waiting for current map", phase="timeout"),
        ApiError("Corrupt grid", phase="protocol"),
        TimeoutError(),
    ],
)
async def test_temporary_failure_retries_with_backoff(hass, mock_client, error) -> None:
    """Temporary failures add no camera, back off, then retry."""

    mock_client.get_occupancy_grid.side_effect = [error, _grid()]
    await _setup(hass, mock_client)
    assert _camera_id(hass) is None

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=GRID_RETRY_AFTER_FAILURE - 10)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_client.get_occupancy_grid.await_count == 1

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=GRID_RETRY_AFTER_FAILURE + 1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_client.get_occupancy_grid.await_count == 2
    assert _camera_id(hass) is not None


async def test_unload_cancels_pending_retry(hass, mock_client) -> None:
    """No discovery runs after the entry is unloaded."""

    mock_client.get_occupancy_grid.side_effect = ApiError("down", phase="transport")
    entry = await _setup(hass, mock_client)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=3))
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.get_occupancy_grid.assert_awaited_once()


async def test_authentication_error_starts_reauth(hass, mock_client) -> None:
    """Authentication failures start Home Assistant reauthentication."""

    mock_client.get_occupancy_grid.side_effect = AuthenticationError("expired")
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)
    with patch.object(entry, "async_start_reauth") as start_reauth:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    start_reauth.assert_called_once_with(hass)
    assert _camera_id(hass) is None

    # No retry loop with bad credentials; reauthentication reloads the entry.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=3))
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.get_occupancy_grid.assert_awaited_once()


async def test_authentication_error_after_success_stops_grid_requests(
    hass, mock_client
) -> None:
    """After an auth failure the existing camera requests no more grids."""

    mock_client.get_occupancy_grid.side_effect = [
        _grid(),
        AuthenticationError("expired"),
    ]
    entry = await _setup(hass, mock_client)
    camera = hass.data["camera"].get_entity(_camera_id(hass))
    grid_maps = camera._grid_maps
    grid_maps.render(TEST_DEVICE.id).fetched = time.monotonic() - 10_000

    with patch.object(entry, "async_start_reauth") as start_reauth:
        assert await camera.async_camera_image() is not None
        await hass.async_block_till_done(wait_background_tasks=True)
        start_reauth.assert_called_once_with(hass)
        assert mock_client.get_occupancy_grid.await_count == 2

        # The stale camera keeps its last image but asks for nothing more,
        # not even forced refreshes, until reauthentication reloads the entry.
        assert await camera.async_camera_image() is not None
        await camera.async_update()
        assert grid_maps.async_request_refresh(TEST_DEVICE.id, force=True) is None
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=3))
        await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_client.get_occupancy_grid.await_count == 2
    start_reauth.assert_called_once()


async def test_camera_caches_png_and_refreshes_when_stale(hass, mock_client) -> None:
    """Images reuse the cached PNG; a stale grid refreshes once in the background."""

    mock_client.get_occupancy_grid.side_effect = None
    mock_client.get_occupancy_grid.return_value = _grid()
    await _setup(hass, mock_client)
    camera_id = _camera_id(hass)
    assert camera_id is not None
    camera = hass.data["camera"].get_entity(camera_id)

    with patch(
        "custom_components.botslab360.camera.render_grid_png",
        wraps=render_grid_png,
    ) as render:
        first = await camera.async_camera_image()
        second = await camera.async_camera_image()
    assert first is second
    render.assert_called_once()
    assert mock_client.get_occupancy_grid.await_count == 1

    grid_maps = camera._grid_maps
    old = time.monotonic() - 10_000
    grid_maps.render(TEST_DEVICE.id).fetched = old
    assert await camera.async_camera_image() is not None
    assert await camera.async_camera_image() is not None
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_client.get_occupancy_grid.await_count == 2
    assert grid_maps.render(TEST_DEVICE.id).fetched > old


async def test_refresh_failure_keeps_last_image_and_backs_off(
    hass, mock_client
) -> None:
    """After a failed refresh the last image stays and no refresh repeats at once."""

    mock_client.get_occupancy_grid.side_effect = [
        _grid(),
        ApiError("down", phase="transport"),
    ]
    await _setup(hass, mock_client)
    camera = hass.data["camera"].get_entity(_camera_id(hass))
    grid_maps = camera._grid_maps
    grid_maps.render(TEST_DEVICE.id).fetched = time.monotonic() - 10_000

    assert await camera.async_camera_image() is not None
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await camera.async_camera_image() is not None
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_client.get_occupancy_grid.await_count == 2


async def test_concurrent_refresh_requests_share_one_fetch(hass, mock_client) -> None:
    """Only one grid fetch runs per robot at a time."""

    release = asyncio.Event()
    calls = 0

    async def slow_grid(device, timeout):
        nonlocal calls
        calls += 1
        if calls > 1:
            await release.wait()
        return _grid()

    mock_client.get_occupancy_grid.side_effect = slow_grid
    await _setup(hass, mock_client)
    camera = hass.data["camera"].get_entity(_camera_id(hass))
    grid_maps = camera._grid_maps

    first = grid_maps.async_request_refresh(TEST_DEVICE.id, force=True)
    second = grid_maps.async_request_refresh(TEST_DEVICE.id, force=True)
    assert first is second
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == 2


async def test_robot_marker_follows_status_position(hass, mock_client) -> None:
    """The robot marker is drawn from the status and hidden at (0, 0)."""

    mock_client.get_occupancy_grid.side_effect = None
    mock_client.get_occupancy_grid.return_value = _grid(dock_position=None)
    await _setup(hass, mock_client)
    camera = hass.data["camera"].get_entity(_camera_id(hass))
    inside = replace(make_status(), position_x=50, position_y=50)
    camera.coordinator.data[TEST_DEVICE.id] = inside

    assert ROBOT in _colours(await camera.async_camera_image())

    status = replace(make_status(), position_x=0, position_y=0)
    camera.coordinator.data[TEST_DEVICE.id] = status
    assert ROBOT not in _colours(await camera.async_camera_image())


async def test_unexpected_error_is_retried(hass, mock_client) -> None:
    """An unexpected error backs off and retries instead of stopping discovery."""

    mock_client.get_occupancy_grid.side_effect = [RuntimeError("boom"), _grid()]
    await _setup(hass, mock_client)
    assert _camera_id(hass) is None

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=GRID_RETRY_AFTER_FAILURE + 1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _camera_id(hass) is not None


async def test_grid_lost_after_success_keeps_camera_and_retries(
    hass, mock_client
) -> None:
    """A later UnsupportedOccupancyGridError keeps the camera and backs off."""

    mock_client.get_occupancy_grid.side_effect = [
        _grid(),
        UnsupportedOccupancyGridError("no grid", phase="unsupported"),
        _grid(),
    ]
    await _setup(hass, mock_client)
    camera = hass.data["camera"].get_entity(_camera_id(hass))
    grid_maps = camera._grid_maps

    assert grid_maps.async_request_refresh(TEST_DEVICE.id, force=True) is not None
    await hass.async_block_till_done(wait_background_tasks=True)
    assert TEST_DEVICE.id not in grid_maps.unsupported
    assert await camera.async_camera_image() is not None

    assert grid_maps.async_request_refresh(TEST_DEVICE.id, force=True) is not None
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_client.get_occupancy_grid.await_count == 3
