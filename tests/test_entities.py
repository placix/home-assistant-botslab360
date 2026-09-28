"""Tests for Botslab 360 vacuum and sensor entities."""

from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfArea, UnitOfTime
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from custom_components.botslab360.const import DOMAIN
from custom_components.botslab360.coordinator import Botslab360Coordinator
from custom_components.botslab360.sensor import (
    SENSOR_DESCRIPTIONS,
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
        sensors["cleaned_area"].native_unit_of_measurement
        == UnitOfArea.SQUARE_METERS
    )
    assert sensors["cleaning_time"].native_value == 321
    assert sensors["cleaning_time"].device_class is SensorDeviceClass.DURATION
    assert sensors["cleaning_time"].native_unit_of_measurement == UnitOfTime.SECONDS
    assert sensors["error_code"].native_value == 0
    assert sensors["error_code"].entity_category is EntityCategory.DIAGNOSTIC
    assert sensors["fan_mode"].native_value == "strong"
    assert sensors["fan_mode"].entity_category is EntityCategory.DIAGNOSTIC

    entities = [vacuum, *sensors.values()]
    for entity in entities:
        assert entity.device_info["identifiers"] == {(DOMAIN, TEST_DEVICE.id)}
    assert vacuum.unique_id == TEST_DEVICE.id
    assert {sensor.unique_id for sensor in sensors.values()} == {
        f"{TEST_DEVICE.id}_{key}" for key in sensors
    }
