"""Tests for the occupancy-grid map of robots without room segmentation."""

from __future__ import annotations

import base64
import time
from dataclasses import replace
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botslab360 import ApiError, AuthenticationError
from homeassistant.components.camera import async_get_image
from homeassistant.helpers import entity_registry as er
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360 import Botslab360RuntimeData
from custom_components.botslab360.camera import Botslab360GridMapCamera
from custom_components.botslab360.const import DOMAIN
from custom_components.botslab360.coordinator import Botslab360Coordinator
from custom_components.botslab360.grid_map import (
    _lz4_block,
    decode_grid_map,
    render_grid_png,
)
from custom_components.botslab360.map import Botslab360MapCache

from .conftest import TEST_CREDENTIALS, TEST_DEVICE, TEST_ROOMS, make_status

WIDTH, HEIGHT = 8, 6


def _literal_block(data: bytes) -> bytes:
    """Encode bytes as a valid literal-only LZ4 block."""

    length = len(data)
    if length < 15:
        return bytes([length << 4]) + data
    rest = length - 15
    ext = b"\xff" * (rest // 255) + bytes([rest % 255])
    return bytes([0xF0]) + ext + data


def _grid() -> bytes:
    """Unknown border, floor inside, one wall cell."""

    cells = bytearray([127] * (WIDTH * HEIGHT))
    for row in range(1, HEIGHT - 1):
        for col in range(1, WIDTH - 1):
            cells[row * WIDTH + col] = 255
    cells[2 * WIDTH + 3] = 0
    return bytes(cells)


def _map_info(grid: bytes | None = None) -> dict:
    block = _literal_block(_grid() if grid is None else grid)
    # The robot sends base64 with spaces in place of "+".
    encoded = base64.b64encode(block).decode().replace("+", " ")
    return {
        "mapId": 1700000000,
        "area": [],
        "width": WIDTH,
        "height": HEIGHT,
        "resolution": 0.05,
        "x_min": -0.2,
        "y_min": -0.1,
        "lz4_len": len(block),
        "map": encoded,
        "chargeHandlePos": [0, 0],
    }


def test_lz4_literals_and_match() -> None:
    """A literal run followed by an overlapping match expands correctly."""

    # 1 literal (0x7f) + match offset 1, length 4 + 7 = 11 -> 12 bytes of 0x7f.
    block = bytes([(1 << 4) | 7, 0x7F, 0x01, 0x00])
    assert _lz4_block(block, 12) == b"\x7f" * 12
    assert _lz4_block(_literal_block(b"x" * 300), 300) == b"x" * 300


@pytest.mark.parametrize(
    ("block", "size"),
    [
        (bytes([(1 << 4) | 7, 0x7F, 0x00, 0x00]), 12),  # offset 0
        (bytes([(1 << 4) | 7, 0x7F, 0x05, 0x00]), 12),  # offset beyond output
        (bytes([(1 << 4) | 7, 0x7F, 0x01, 0x00]), 8),  # grows past out_len
        (bytes([0x50, 1, 2]), 5),  # truncated literals
        (bytes([0x20, 1, 2]), 5),  # size mismatch
    ],
)
def test_lz4_rejects_corrupt_blocks(block: bytes, size: int) -> None:
    """Corrupt blocks raise ApiError instead of producing a wrong grid."""

    with pytest.raises(ApiError):
        _lz4_block(block, size)


def test_decode_grid_map_geometry() -> None:
    """MapInfo fields become a scaled image with the dock position."""

    grid = decode_grid_map(_map_info())

    assert (grid.width, grid.height) == (WIDTH, HEIGHT)
    assert grid.map_id == 1700000000
    assert grid.dock == (0, 0)
    assert grid.base.size == (WIDTH * grid.scale, HEIGHT * grid.scale)


@pytest.mark.parametrize(
    "change",
    [
        {"width": 0},
        {"height": 5000, "width": 5000},
        {"resolution": 0},
        {"resolution": float("nan")},
        {"lz4_len": 1},
        {"map": "!!!"},
    ],
)
def test_decode_grid_map_rejects_bad_fields(change: dict) -> None:
    """Invalid MapInfo fields raise ApiError (phase protocol)."""

    with pytest.raises(ApiError):
        decode_grid_map(_map_info() | change)


def test_decode_grid_map_requires_grid_fields() -> None:
    """A MapInfo without the grid is not decoded."""

    info = _map_info()
    del info["map"]
    with pytest.raises(ApiError):
        decode_grid_map(info)


def test_render_draws_robot_and_dock() -> None:
    """The PNG has the base size and coloured robot/dock markers."""

    grid = decode_grid_map(_map_info())
    png = render_grid_png(grid, (100, 100))

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(png)) as image:
        assert image.size == grid.base.size
        colours = {colour for _, colour in image.convert("RGB").getcolors(100_000)}
    assert (230, 60, 60) in colours  # robot
    assert (60, 200, 90) in colours  # dock


def _camera(hass, mock_client) -> Botslab360GridMapCamera:
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    coordinator.data = {TEST_DEVICE.id: make_status()}
    entry.runtime_data = Botslab360RuntimeData(
        mock_client,
        coordinator,
        Botslab360MapCache(hass, mock_client),
        unsegmented_devices=frozenset({TEST_DEVICE.id}),
    )
    entry.add_to_hass(hass)
    camera = Botslab360GridMapCamera(entry, coordinator, TEST_DEVICE)
    camera.hass = hass
    return camera


async def test_grid_camera_fetches_once_and_caches(hass, mock_client) -> None:
    """First image waits for the fetch; later images reuse the cached PNG."""

    camera = _camera(hass, mock_client)
    fetch = AsyncMock(return_value=_map_info())
    with patch("custom_components.botslab360.camera.fetch_map_info", fetch):
        first = await camera.async_camera_image()
        second = await camera.async_camera_image()

    assert first is not None and first.startswith(b"\x89PNG\r\n\x1a\n")
    assert first is second
    fetch.assert_awaited_once()
    assert camera.unique_id == f"{TEST_DEVICE.id}_map"


async def test_grid_camera_refreshes_when_stale(hass, mock_client) -> None:
    """An idle map older than the idle max age is refreshed in the background."""

    camera = _camera(hass, mock_client)
    fetch = AsyncMock(return_value=_map_info())
    with patch("custom_components.botslab360.camera.fetch_map_info", fetch):
        assert await camera.async_camera_image() is not None
        camera._grid.fetched = time.monotonic() - 10_000  # noqa: SLF001
        assert await camera.async_camera_image() is not None
        await hass.async_block_till_done()

    assert fetch.await_count == 2


async def test_grid_camera_failure_backs_off(hass, mock_client) -> None:
    """A failed fetch returns no image and is not retried immediately."""

    camera = _camera(hass, mock_client)
    fetch = AsyncMock(side_effect=ApiError("Timed out waiting for map", phase="timeout"))
    with patch("custom_components.botslab360.camera.fetch_map_info", fetch):
        assert await camera.async_camera_image() is None
        assert await camera.async_camera_image() is None

    fetch.assert_awaited_once()


async def test_grid_camera_authentication_error_starts_reauth(
    hass, mock_client
) -> None:
    """Authentication failures start Home Assistant reauthentication."""

    camera = _camera(hass, mock_client)
    camera._config_entry.async_start_reauth = MagicMock()  # noqa: SLF001
    fetch = AsyncMock(side_effect=AuthenticationError("expired"))
    with patch("custom_components.botslab360.camera.fetch_map_info", fetch):
        assert await camera.async_camera_image() is None

    camera._config_entry.async_start_reauth.assert_called_once_with(hass)  # noqa: SLF001


async def test_grid_camera_only_for_unsegmented_robots(hass, mock_client) -> None:
    """Setup creates a grid camera for the no-room robot and none for the other."""

    room_robot = replace(TEST_DEVICE, id="room-capable-device", name="Room Robot")
    mock_client.get_devices.return_value = [TEST_DEVICE, room_robot]
    mock_client.get_rooms.side_effect = lambda device: (
        [] if device.id == TEST_DEVICE.id else TEST_ROOMS
    )
    mock_client.get_status.side_effect = lambda device_id: replace(
        make_status(), device_id=device_id
    )
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.unsegmented_devices == frozenset({TEST_DEVICE.id})
    registry = er.async_get(hass)
    camera_id = registry.async_get_entity_id("camera", DOMAIN, f"{TEST_DEVICE.id}_map")
    assert camera_id is not None
    assert (
        registry.async_get_entity_id("camera", DOMAIN, f"{room_robot.id}_map") is None
    )

    with patch(
        "custom_components.botslab360.camera.fetch_map_info",
        AsyncMock(return_value=_map_info()),
    ):
        image = await async_get_image(hass, camera_id)
    assert image.content_type == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
