"""Tests for Botslab 360 DHCP discovery and MAC registration."""

import json
from fnmatch import fnmatchcase
from pathlib import Path

from botslab360 import ApiError, Device, NetworkInfo
from homeassistant.config_entries import SOURCE_DHCP, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import DOMAIN

from .conftest import (
    TEST_ACCOUNT_FINGERPRINT,
    TEST_DEVICE,
    TEST_NATIVE_ENTRY_DATA,
    TEST_NATIVE_INPUT,
    TEST_ROOMS,
)

DISCOVERED_MAC = "b0:59:47:c1:2e:c1"
DISCOVERY = DhcpServiceInfo(
    ip="192.168.1.176",
    hostname="360_cleanrobot_x9",
    macaddress="b05947c12ec1",
)


def _network_info(mac_address: str = DISCOVERED_MAC) -> NetworkInfo:
    """Return representative public network information."""

    return NetworkInfo(
        station_ip="192.168.1.176",
        station_mac=mac_address,
        station_ssid="test-network",
        station_signal=-48,
    )


async def _start_dhcp_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DISCOVERY,
    )


async def _reach_room_assignment(hass, mock_client):
    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )
    mock_client.get_network_info.return_value = _network_info()
    return await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )


def test_manifest_uses_narrow_combined_dhcp_matcher() -> None:
    """Test hostname and MAC prefix are both required by the manifest matcher."""

    manifest = json.loads(
        Path("custom_components/botslab360/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["dhcp"] == [
        {
            "hostname": "360_cleanrobot_x9",
            "macaddress": "B05947*",
        }
    ]

    matcher = manifest["dhcp"][0]

    def matches(hostname: str, macaddress: str) -> bool:
        return fnmatchcase(hostname.casefold(), matcher["hostname"]) and fnmatchcase(
            macaddress.upper().replace(":", ""), matcher["macaddress"]
        )

    assert matches("360_CleanRobot_X9", DISCOVERED_MAC)
    assert not matches("360_cleanrobot_other", DISCOVERED_MAC)
    assert not matches("360_cleanrobot_x9", "00:11:22:33:44:55")


async def test_dhcp_starts_confirmed_existing_login_flow(hass, mock_client) -> None:
    """Test discovery remains transient and requires explicit user configuration."""

    result = await _start_dhcp_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"
    flow = hass.config_entries.flow.async_get(result["flow_id"])
    assert flow["context"]["title_placeholders"] == {"name": "360 CleanRobot X9"}
    assert not hass.config_entries.async_entries(DOMAIN)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert not hass.config_entries.async_entries(DOMAIN)
    mock_client.authenticate.assert_not_awaited()


async def test_dhcp_matching_robot_continues_and_registers_physical_mac(
    hass, mock_client
) -> None:
    """Test authentication verifies and registers the discovered physical robot."""

    result = await _reach_room_assignment(hass, mock_client)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "room_areas"
    mock_client.get_network_info.assert_awaited_once_with(TEST_DEVICE)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == TEST_ACCOUNT_FINGERPRINT
    registry = dr.async_get(hass)
    robot = registry.async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        result["result"].entry_id,
    )
    assert robot is not None
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) in robot.connections

    for room in TEST_ROOMS:
        child = registry.async_get_child_device_by_identifier(
            (DOMAIN, f"{TEST_DEVICE.id}_room_{room.id}"),
            result["result"].entry_id,
        )
        assert child is not None
        assert not hasattr(child, "connections")


async def test_dhcp_rejects_account_without_matching_robot(hass, mock_client) -> None:
    """Test a discovered robot cannot be configured through an unrelated account."""

    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )
    mock_client.get_network_info.return_value = _network_info("00:11:22:33:44:55")

    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "discovered_device_not_found"}
    assert not hass.config_entries.async_entries(DOMAIN)
    mock_client.close.assert_awaited_once()


async def test_dhcp_rejects_ambiguous_mac(hass, mock_client) -> None:
    """Test duplicate network identities fail instead of selecting a robot."""

    second_device = Device(
        id="second-test-device",
        name="Second Test Robot",
        model="Test Model",
        online=True,
    )
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_network_info.return_value = _network_info()
    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )

    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "ambiguous_discovered_device"}
    assert mock_client.get_network_info.await_count == 2


async def test_existing_registered_robot_suppresses_dhcp_discovery(
    hass, mock_client
) -> None:
    """Test a MAC already owned by a Botslab device is not rediscovered."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, TEST_DEVICE.id)},
        connections={(dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC)},
    )

    result = await _start_dhcp_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_client.authenticate.assert_not_awaited()


async def test_existing_account_is_verified_before_mac_registration(
    hass, mock_client
) -> None:
    """Test an existing account gains the MAC only after robot verification."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, TEST_DEVICE.id)},
    )

    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )
    mock_client.get_network_info.return_value = _network_info()
    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    robot = registry.async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        entry.entry_id,
    )
    assert robot is not None
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) in robot.connections
    mock_client.get_network_info.assert_awaited_once_with(TEST_DEVICE)


async def test_existing_entry_setup_best_effort_registers_mac(
    hass, mock_client
) -> None:
    """Test existing installations gain a network connection during setup."""

    mock_client.get_network_info.return_value = _network_info()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    robot = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        entry.entry_id,
    )
    assert robot is not None
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) in robot.connections


async def test_network_info_failure_does_not_break_existing_setup(
    hass, mock_client
) -> None:
    """Test optional network identity lookup cannot make cloud setup unavailable."""

    mock_client.get_network_info.side_effect = ApiError("unavailable")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("vacuum.test_robot") is not None
    robot = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        entry.entry_id,
    )
    assert robot is not None
    assert not robot.connections


async def test_existing_setup_registers_multiple_robots_independently(
    hass, mock_client
) -> None:
    """Test MAC connections stay associated with their own physical robot."""

    second_device = Device(
        id="second-test-device",
        name="Second Test Robot",
        model="Test Model",
        online=True,
    )
    second_mac = "b0:59:47:00:00:02"
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_network_info.side_effect = [
        _network_info(DISCOVERED_MAC),
        _network_info(second_mac),
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = dr.async_get(hass)
    first = registry.async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        entry.entry_id,
    )
    second = registry.async_get_device_by_identifier(
        (DOMAIN, second_device.id),
        entry.entry_id,
    )
    assert first is not None
    assert second is not None
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) in first.connections
    assert (dr.CONNECTION_NETWORK_MAC, second_mac) in second.connections
    assert (dr.CONNECTION_NETWORK_MAC, second_mac) not in first.connections
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) not in second.connections
