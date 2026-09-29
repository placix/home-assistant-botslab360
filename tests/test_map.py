"""Tests for the rendered room-map camera and geometry helpers."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from botslab360 import ApiError, AuthenticationError
from homeassistant.const import Platform
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360 import Botslab360RuntimeData
from custom_components.botslab360.camera import Botslab360MapCamera
from custom_components.botslab360.const import DOMAIN, PLATFORMS
from custom_components.botslab360.coordinator import Botslab360Coordinator
from custom_components.botslab360.map import (
    MAP_SIZE,
    Botslab360MapCache,
    map_card_selection,
    polygon_centroid,
    render_room_map,
)

from .conftest import TEST_CREDENTIALS, TEST_DEVICE, TEST_ROOMS, make_status


def test_polygon_renderer_produces_png_calibration_and_selections() -> None:
    """Test PNG rendering and the complete Map Card coordinate contract."""

    rendered = render_room_map(TEST_ROOMS)

    assert rendered.image.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(rendered.image)) as image:
        assert image.size == (MAP_SIZE, MAP_SIZE)
        assert image.format == "PNG"
        assert len(image.getcolors(maxcolors=1_000_000)) > 2
    assert len(rendered.calibration_points) == 3
    assert rendered.calibration_points[0]["vacuum"] == {"x": 0, "y": 0}
    assert (
        rendered.calibration_points[0]["map"]["x"]
        > (rendered.calibration_points[1]["map"]["x"])
    )
    assert (
        rendered.calibration_points[0]["map"]["y"]
        == (rendered.calibration_points[1]["map"]["y"])
    )
    assert (
        rendered.calibration_points[0]["map"]["x"]
        == (rendered.calibration_points[2]["map"]["x"])
    )
    assert rendered.predefined_selections[0] == {
        "id": 1,
        "outline": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
        "label": {"text": "Bad", "x": 2000.0, "y": 1500.0},
    }


def test_centroid_handles_triangle_and_degenerate_polygon() -> None:
    """Test robust label placement for ordinary and degenerate polygons."""

    assert polygon_centroid(((0, 0), (6, 0), (0, 6))) == (2.0, 2.0)
    assert polygon_centroid(((0, 0), (2, 0), (4, 0))) == (2.0, 0.0)


def test_renderer_handles_rooms_without_geometry() -> None:
    """Test malformed/missing polygons produce a stable placeholder PNG."""

    room = SimpleNamespace(id=3, name="Unknown", vertices=None)
    rendered = render_room_map([room])

    assert rendered.image.startswith(b"\x89PNG\r\n\x1a\n")
    assert rendered.calibration_points == ()
    assert rendered.predefined_selections == ()
    assert map_card_selection(room) is None


async def test_camera_fetches_once_then_returns_cached_image(hass, mock_client) -> None:
    """Test first-use map retrieval and cached camera attributes."""

    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    coordinator.data = {TEST_DEVICE.id: make_status()}
    cache = Botslab360MapCache(hass, mock_client)
    entry.runtime_data = Botslab360RuntimeData(mock_client, coordinator, cache)
    camera = Botslab360MapCamera(entry, coordinator, TEST_DEVICE)
    camera.hass = hass

    first = await camera.async_camera_image()
    second = await camera.async_camera_image()

    assert first == second
    assert first is not None and first.startswith(b"\x89PNG\r\n\x1a\n")
    mock_client.get_rooms.assert_awaited_once_with(TEST_DEVICE)
    assert len(camera.extra_state_attributes["calibration_points"]) == 3
    assert camera.extra_state_attributes["rooms"] == [
        {"id": 1, "name": "Bad"},
        {"id": 6, "name": "Closet"},
    ]
    assert camera.unique_id == f"{TEST_DEVICE.id}_map"
    assert camera.device_info["identifiers"] == {(DOMAIN, TEST_DEVICE.id)}


async def test_failed_camera_refresh_retains_last_good_image(hass, mock_client) -> None:
    """Test a map API failure cannot erase the cached rendering."""

    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    coordinator.data = {TEST_DEVICE.id: make_status()}
    cache = Botslab360MapCache(hass, mock_client)
    entry.runtime_data = Botslab360RuntimeData(mock_client, coordinator, cache)
    camera = Botslab360MapCamera(entry, coordinator, TEST_DEVICE)
    camera.hass = hass
    original = await camera.async_camera_image()
    mock_client.get_rooms.side_effect = ApiError("map unavailable")

    await camera.async_update()

    assert await camera.async_camera_image() == original


async def test_camera_authentication_error_starts_reauth(hass, mock_client) -> None:
    """Test map authentication failures start Home Assistant reauth."""

    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    coordinator.data = {TEST_DEVICE.id: make_status()}
    cache = Botslab360MapCache(hass, mock_client)
    entry.runtime_data = Botslab360RuntimeData(mock_client, coordinator, cache)
    camera = Botslab360MapCamera(entry, coordinator, TEST_DEVICE)
    camera.hass = hass
    mock_client.get_rooms.side_effect = AuthenticationError("expired")
    entry.async_start_reauth = MagicMock()

    assert await camera.async_camera_image() is None

    entry.async_start_reauth.assert_called_once_with(hass)


async def test_camera_platform_is_not_loaded_for_multiple_devices(
    hass, mock_client
) -> None:
    """Test map code remains available while camera entities stay disabled."""

    second = TEST_DEVICE.__class__(
        id="second-test-device",
        name="Second Robot",
        model="Test Model",
        online=True,
    )
    mock_client.get_devices.return_value = [TEST_DEVICE, second]
    mock_client.get_status.side_effect = [make_status(), make_status()]
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert Platform.CAMERA not in PLATFORMS
    assert Platform.SWITCH in PLATFORMS
    assert hass.states.get("camera.test_robot_map") is None
    assert hass.states.get("camera.second_robot_map") is None
    assert mock_client.get_rooms.await_count == 2


def test_map_implementation_remains_importable() -> None:
    """Test deferred rendering code remains available for future use."""

    assert Botslab360MapCamera is not None
    assert render_room_map is not None
