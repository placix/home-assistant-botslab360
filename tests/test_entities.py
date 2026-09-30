"""Tests for Botslab 360 vacuum and sensor entities."""

from unittest.mock import AsyncMock

import pytest
from botslab360 import Room
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.areas import DiscoveredRoom
from custom_components.botslab360.const import DOMAIN
from custom_components.botslab360.coordinator import Botslab360Coordinator
from custom_components.botslab360.sensor import (
    ROOM_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    Botslab360RoomSensor,
    Botslab360Sensor,
)
from custom_components.botslab360.vacuum import (
    SUPPORTED_FEATURES,
    Botslab360Vacuum,
    vacuum_activity,
)

from .conftest import TEST_CREDENTIALS, TEST_DEVICE, make_status


@pytest.mark.parametrize(
    ("state", "error_code", "expected"),
    [
        ("sweep", 0, VacuumActivity.CLEANING),
        ("mop", 0, VacuumActivity.CLEANING),
        ("charge", 0, VacuumActivity.DOCKED),
        ("fullcharge", 0, VacuumActivity.DOCKED),
        ("idle", 0, VacuumActivity.IDLE),
        ("dormant", 0, VacuumActivity.IDLE),
        ("pause", 0, VacuumActivity.PAUSED),
        ("backcharge", 0, VacuumActivity.RETURNING),
        ("fault", 0, VacuumActivity.ERROR),
        ("idle", 9, VacuumActivity.ERROR),
        ("future-mode", 0, None),
        (None, 0, None),
    ],
)
def test_state_mapping(state, error_code, expected) -> None:
    """Test Botslab states map safely to Home Assistant activities."""

    assert vacuum_activity(make_status(state=state, error_code=error_code)) is expected


@pytest.fixture
def vacuum(hass, mock_client) -> Botslab360Vacuum:
    """Create a vacuum entity with coordinator data."""

    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    coordinator.data = {TEST_DEVICE.id: make_status()}
    coordinator.async_request_refresh = AsyncMock()
    return Botslab360Vacuum(entry, coordinator, TEST_DEVICE)


async def test_vacuum_start(vacuum, mock_client) -> None:
    """Test start calls the library and refreshes status."""

    await vacuum.async_start()

    mock_client.start_cleaning.assert_awaited_once_with(TEST_DEVICE.id)
    mock_client.resume.assert_not_awaited()
    vacuum.coordinator.async_request_refresh.assert_awaited_once()


async def test_vacuum_resumes_from_pause(vacuum, mock_client) -> None:
    """Test start resumes a paused robot."""

    vacuum.coordinator.data = {TEST_DEVICE.id: make_status(state="pause")}
    await vacuum.async_start()

    mock_client.resume.assert_awaited_once_with(TEST_DEVICE.id)
    mock_client.start_cleaning.assert_not_awaited()


@pytest.mark.parametrize(
    ("entity_method", "client_method"),
    [
        ("async_pause", "pause"),
        ("async_return_to_base", "return_to_dock"),
        ("async_locate", "locate"),
    ],
)
async def test_vacuum_commands(
    vacuum, mock_client, entity_method, client_method
) -> None:
    """Test each supported control delegates to the library."""

    await getattr(vacuum, entity_method)()

    getattr(mock_client, client_method).assert_awaited_once_with(TEST_DEVICE.id)
    vacuum.coordinator.async_request_refresh.assert_awaited_once()


def test_supported_features_exclude_stop_and_fan_speed() -> None:
    """Test the initial feature set is intentionally narrow."""

    assert SUPPORTED_FEATURES & VacuumEntityFeature.STATE
    assert SUPPORTED_FEATURES & VacuumEntityFeature.START
    assert SUPPORTED_FEATURES & VacuumEntityFeature.PAUSE
    assert SUPPORTED_FEATURES & VacuumEntityFeature.RETURN_HOME
    assert SUPPORTED_FEATURES & VacuumEntityFeature.LOCATE
    assert not SUPPORTED_FEATURES & VacuumEntityFeature.STOP
    assert not SUPPORTED_FEATURES & VacuumEntityFeature.FAN_SPEED


def test_sensor_values_and_metadata(hass, mock_client) -> None:
    """Test sensor values, units, categories, device info, and unique IDs."""

    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    coordinator.data = {TEST_DEVICE.id: make_status()}
    vacuum = Botslab360Vacuum(entry, coordinator, TEST_DEVICE)
    sensors = {
        description.key: Botslab360Sensor(coordinator, TEST_DEVICE, description)
        for description in SENSOR_DESCRIPTIONS
    }

    assert sensors["battery"].native_value == 73
    assert sensors["battery"].device_class is SensorDeviceClass.BATTERY
    assert sensors["battery"].native_unit_of_measurement == PERCENTAGE
    assert sensors["cleaned_area"].native_value == 42
    assert sensors["cleaned_area"].device_class is SensorDeviceClass.AREA
    assert (
        sensors["cleaned_area"].native_unit_of_measurement == UnitOfArea.SQUARE_METERS
    )
    assert sensors["cleaning_time"].native_value == 321
    assert sensors["cleaning_time"].device_class is SensorDeviceClass.DURATION
    assert sensors["cleaning_time"].native_unit_of_measurement == UnitOfTime.SECONDS
    assert sensors["error_code"].native_value == 0
    assert sensors["error_code"].entity_category is EntityCategory.DIAGNOSTIC
    assert sensors["fan_mode"].native_value == "strong"
    assert sensors["fan_mode"].entity_category is EntityCategory.DIAGNOSTIC
    assert sensors["fan_mode"].entity_registry_enabled_default is False
    assert sensors["raw_state"].native_value == "idle"
    assert sensors["raw_total_cleaned_area"].native_value == 4200
    assert (
        sensors["raw_total_cleaned_area"].state_class
        is SensorStateClass.TOTAL_INCREASING
    )
    assert sensors["total_cleaning_time"].native_value == 3600
    assert sensors["raw_sub_state"].native_value == "smart"
    assert sensors["raw_last_sub_state"].native_value == "total"
    assert sensors["position_x"].native_value == 120
    assert sensors["position_y"].native_value == 340
    assert sensors["heading"].native_value == 90
    assert sensors["raw_timer_status"].native_value == 1
    assert sensors["raw_auto_boost"].native_value == 0
    assert sensors["raw_mop_status"].native_value is None

    default_enabled = {"battery", "cleaned_area", "cleaning_time", "error_code"}
    for key, sensor in sensors.items():
        assert sensor.entity_registry_enabled_default is (key in default_enabled)
        if key not in default_enabled:
            assert sensor.entity_category is EntityCategory.DIAGNOSTIC

    entities = [vacuum, *sensors.values()]
    for entity in entities:
        assert entity.device_info["identifiers"] == {(DOMAIN, TEST_DEVICE.id)}
    assert vacuum.unique_id == TEST_DEVICE.id
    assert {sensor.unique_id for sensor in sensors.values()} == {
        f"{TEST_DEVICE.id}_{key}" for key in sensors
    }


def test_room_diagnostic_sensors_are_disabled_and_use_child_device(
    hass, mock_client
) -> None:
    """Test restrained room diagnostics preserve raw values on the room device."""

    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    room = Room(
        id=7,
        name="Utility",
        room_type="utility",
        clean_times=1,
        fan_mode="auto",
        water_pump=4,
        vertices=((0, 0), (1, 0), (1, 1)),
        mode="mode_tiny_carpet",
    )
    discovered = DiscoveredRoom(TEST_DEVICE, room)
    sensors = {
        description.key: Botslab360RoomSensor(
            coordinator,
            discovered,
            "parent-device-id",
            description,
        )
        for description in ROOM_SENSOR_DESCRIPTIONS
    }

    assert sensors["room_id"].native_value == 7
    assert sensors["room_type"].native_value == "utility"
    assert sensors["raw_sweep_area_mode"].native_value == "mode_tiny_carpet"
    assert sensors["raw_water_pump"].native_value == 4
    assert sensors["polygon_vertex_count"].native_value == 3
    for key, sensor in sensors.items():
        assert sensor.entity_category is EntityCategory.DIAGNOSTIC
        assert sensor.entity_registry_enabled_default is False
        assert sensor.unique_id == f"{TEST_DEVICE.id}_room_7_{key}"
        assert sensor.device_info["identifiers"] == {
            (DOMAIN, f"{TEST_DEVICE.id}_room_7")
        }
        assert sensor.device_info["parent_device_id"] == "parent-device-id"


async def test_entity_registry_defaults_and_device_hierarchy(hass, mock_client) -> None:
    """Test everyday entities are enabled and diagnostics use the right device."""

    mock_client.get_status.return_value = make_status(mop_status=1)
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    parent_id = dr.async_get_device_id_by_identifier(
        hass,
        (DOMAIN, TEST_DEVICE.id),
        config_entry_id=entry.entry_id,
    )
    room_device = devices.async_get_child_device_by_identifier(
        (DOMAIN, f"{TEST_DEVICE.id}_room_1"),
        entry.entry_id,
    )
    assert parent_id is not None
    assert room_device is not None

    battery_id = entities.async_get_entity_id(
        "sensor", DOMAIN, f"{TEST_DEVICE.id}_battery"
    )
    raw_state_id = entities.async_get_entity_id(
        "sensor", DOMAIN, f"{TEST_DEVICE.id}_raw_state"
    )
    room_id = entities.async_get_entity_id(
        "sensor", DOMAIN, f"{TEST_DEVICE.id}_room_1_room_id"
    )
    mop_id = entities.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{TEST_DEVICE.id}_wiping_assembly"
    )
    assert battery_id is not None
    assert raw_state_id is not None
    assert room_id is not None
    assert mop_id is not None

    assert entities.async_get(battery_id).disabled_by is None
    assert entities.async_get(battery_id).device_id == parent_id
    assert (
        entities.async_get(raw_state_id).disabled_by
        is RegistryEntryDisabler.INTEGRATION
    )
    assert entities.async_get(raw_state_id).device_id == parent_id
    assert entities.async_get(room_id).disabled_by is RegistryEntryDisabler.INTEGRATION
    assert entities.async_get(room_id).device_id == room_device.id
    assert entities.async_get(mop_id).disabled_by is None
    assert entities.async_get(mop_id).device_id == parent_id
