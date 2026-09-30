"""Tests for Botslab 360 binary sensors."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.binary_sensor import (
    Botslab360WipingAssemblyBinarySensor,
)
from custom_components.botslab360.const import DOMAIN
from custom_components.botslab360.coordinator import Botslab360Coordinator

from .conftest import TEST_CREDENTIALS, TEST_DEVICE, make_status


def _sensor(hass, mock_client) -> Botslab360WipingAssemblyBinarySensor:
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    coordinator = Botslab360Coordinator(hass, entry, mock_client)
    return Botslab360WipingAssemblyBinarySensor(coordinator, TEST_DEVICE)


def test_wiping_assembly_presence_states(hass, mock_client) -> None:
    """Test only confirmed mop status values produce binary state."""

    sensor = _sensor(hass, mock_client)
    assert isinstance(sensor, BinarySensorEntity)

    sensor.coordinator.data = {TEST_DEVICE.id: make_status(mop_status=0)}
    assert sensor.is_on is False
    sensor.coordinator.data = {TEST_DEVICE.id: make_status(mop_status=1)}
    assert sensor.is_on is True
    sensor.coordinator.data = {TEST_DEVICE.id: make_status(mop_status=2)}
    assert sensor.is_on is None
    sensor.coordinator.data = {TEST_DEVICE.id: make_status(mop_status=None)}
    assert sensor.is_on is None


def test_wiping_assembly_metadata(hass, mock_client) -> None:
    """Test the useful sensor is enabled and attached to the physical robot."""

    sensor = _sensor(hass, mock_client)

    assert sensor.unique_id == f"{TEST_DEVICE.id}_wiping_assembly"
    assert sensor.entity_registry_enabled_default is True
    assert sensor.device_info["identifiers"] == {(DOMAIN, TEST_DEVICE.id)}
