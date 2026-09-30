"""Tests for Botslab 360 DHCP discovery and MAC registration."""

import json
from fnmatch import fnmatchcase
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botslab360 import ApiError, AuthenticationError, Device, NetworkInfo
from homeassistant.config_entries import SOURCE_DHCP, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360 import Botslab360RuntimeData
from custom_components.botslab360.const import DOMAIN
from custom_components.botslab360.coordinator import Botslab360Coordinator

from .conftest import (
    TEST_ACCOUNT_FINGERPRINT,
    TEST_DEVICE,
    TEST_NATIVE_ENTRY_DATA,
    TEST_NATIVE_INPUT,
    TEST_ROOMS,
    make_mock_client,
)

DISCOVERED_MAC = "b0:59:47:c1:2e:c1"
DISCOVERY = DhcpServiceInfo(
    ip="192.168.1.176",
    hostname="360_cleanrobot_x9",
    macaddress="b05947c12ec1",
)


def _network_info(mac_address: str | None = DISCOVERED_MAC) -> NetworkInfo:
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


async def _complete_room_assignment(hass, result):
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    return result


def _set_entry_runtime(hass, entry, client, *devices: Device) -> None:
    """Give an existing entry the public runtime data used by DHCP matching."""

    coordinator = Botslab360Coordinator(hass, entry, client)
    coordinator.devices = {device.id: device for device in devices}
    entry.runtime_data = Botslab360RuntimeData(client, coordinator, MagicMock())


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

    result = await _complete_room_assignment(hass, result)

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


async def test_dhcp_unmatched_mac_continues_without_lan_association(
    hass, mock_client
) -> None:
    """Test a valid account continues when its robots do not match the DHCP MAC."""

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
    assert result["step_id"] == "room_areas"

    result = await _complete_room_assignment(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    robot = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        result["result"].entry_id,
    )
    assert robot is not None
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) not in robot.connections


async def test_dhcp_ambiguous_mac_continues_without_guessing(hass, mock_client) -> None:
    """Test duplicate network identities do not block setup or select a robot."""

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
    assert result["step_id"] == "room_areas"
    assert mock_client.get_network_info.await_count == 2

    result = await _complete_room_assignment(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    registry = dr.async_get(hass)
    for device in (TEST_DEVICE, second_device):
        robot = registry.async_get_device_by_identifier(
            (DOMAIN, device.id),
            result["result"].entry_id,
        )
        assert robot is not None
        assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) not in robot.connections


@pytest.mark.parametrize(
    "network_error",
    [
        ApiError("unavailable"),
        AuthenticationError("unavailable"),
        TimeoutError(),
        OSError("unavailable"),
    ],
)
async def test_dhcp_network_info_failure_continues_without_lan_association(
    hass, mock_client, network_error
) -> None:
    """Test unavailable network information does not block account setup."""

    mock_client.get_network_info.side_effect = network_error
    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )

    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["step_id"] == "room_areas"
    result = await _complete_room_assignment(hass, result)
    robot = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        result["result"].entry_id,
    )
    assert robot is not None
    assert not robot.connections
    mock_client.get_network_info.assert_awaited_once_with(TEST_DEVICE)


async def test_dhcp_missing_station_mac_continues_without_lan_association(
    hass, mock_client
) -> None:
    """Test a missing station MAC leaves the discovered robot unassociated."""

    mock_client.get_network_info.return_value = _network_info(None)
    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )

    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["step_id"] == "room_areas"
    result = await _complete_room_assignment(hass, result)
    robot = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        result["result"].entry_id,
    )
    assert robot is not None
    assert not robot.connections


async def test_dhcp_invalid_credentials_remain_authentication_error(
    hass, mock_client
) -> None:
    """Test optional network verification does not soften login failures."""

    mock_client.authenticate.side_effect = AuthenticationError("invalid")
    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )

    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}
    mock_client.get_network_info.assert_not_awaited()


async def test_dhcp_account_without_devices_remains_no_devices(
    hass, mock_client
) -> None:
    """Test an authenticated account must still contain a supported robot."""

    mock_client.get_devices.return_value = []
    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )

    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices"}
    mock_client.get_network_info.assert_not_awaited()


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


async def test_loaded_entry_matches_and_registers_dhcp_mac(hass, mock_client) -> None:
    """Test runtime network identity suppresses discovery before credential UI."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    original = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, TEST_DEVICE.id)},
    )
    mock_client.get_network_info.return_value = _network_info()
    _set_entry_runtime(hass, entry, mock_client, TEST_DEVICE)

    result = await _start_dhcp_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    robot = registry.async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        entry.entry_id,
    )
    assert robot is not None
    assert robot.id == original.id
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) in robot.connections
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    mock_client.get_network_info.assert_awaited_once_with(TEST_DEVICE)


async def test_loaded_entry_nonmatching_mac_allows_discovery(hass, mock_client) -> None:
    """Test a different configured robot does not suppress genuine discovery."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, TEST_DEVICE.id)},
    )
    mock_client.get_network_info.return_value = _network_info("00:11:22:33:44:55")
    _set_entry_runtime(hass, entry, mock_client, TEST_DEVICE)

    result = await _start_dhcp_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"
    mock_client.get_network_info.assert_awaited_once_with(TEST_DEVICE)


@pytest.mark.parametrize(
    "network_error",
    [
        ApiError("unavailable"),
        AuthenticationError("unavailable"),
        TimeoutError(),
        OSError("unavailable"),
    ],
)
async def test_loaded_entry_network_error_does_not_false_match(
    hass, mock_client, network_error
) -> None:
    """Test runtime identity failures do not crash or suppress discovery."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    _set_entry_runtime(hass, entry, mock_client, TEST_DEVICE)
    mock_client.get_network_info.side_effect = network_error

    result = await _start_dhcp_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"
    assert not dr.async_get(hass).async_get_device_by_connection(
        (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC),
        entry.entry_id,
    )


async def test_multiple_entries_only_exact_runtime_mac_suppresses_dhcp(
    hass, mock_client
) -> None:
    """Test exact MAC matching selects one robot across configured accounts."""

    first_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="first-account",
        data=TEST_NATIVE_ENTRY_DATA,
    )
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="second-account",
        data=TEST_NATIVE_ENTRY_DATA,
    )
    first_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)
    second_device = Device(
        id="second-test-device",
        name="Second Test Robot",
        model="Test Model",
        online=True,
    )
    second_client = make_mock_client()
    mock_client.get_network_info.return_value = _network_info("00:11:22:33:44:55")
    second_client.get_network_info.return_value = _network_info()
    _set_entry_runtime(hass, first_entry, mock_client, TEST_DEVICE)
    _set_entry_runtime(hass, second_entry, second_client, second_device)

    result = await _start_dhcp_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    registry = dr.async_get(hass)
    assert (
        registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC),
            second_entry.entry_id,
        )
        is not None
    )
    assert (
        registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC),
            first_entry.entry_id,
        )
        is None
    )


async def test_ambiguous_runtime_mac_does_not_guess(hass, mock_client) -> None:
    """Test duplicate runtime MAC reports do not select an existing robot."""

    first_entry = MockConfigEntry(domain=DOMAIN, unique_id="first", data={})
    second_entry = MockConfigEntry(domain=DOMAIN, unique_id="second", data={})
    first_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)
    second_client = make_mock_client()
    second_device = Device("second-test-device", "Second Robot", "Test Model", True)
    mock_client.get_network_info.return_value = _network_info()
    second_client.get_network_info.return_value = _network_info()
    _set_entry_runtime(hass, first_entry, mock_client, TEST_DEVICE)
    _set_entry_runtime(hass, second_entry, second_client, second_device)

    result = await _start_dhcp_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"
    registry = dr.async_get(hass)
    assert (
        registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC),
            first_entry.entry_id,
        )
        is None
    )
    assert (
        registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC),
            second_entry.entry_id,
        )
        is None
    )


async def test_repeated_dhcp_uses_learned_registry_connection(
    hass, mock_client
) -> None:
    """Test a learned MAC makes later discovery abort without API work."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    _set_entry_runtime(hass, entry, mock_client, TEST_DEVICE)
    mock_client.get_network_info.return_value = _network_info()

    first = await _start_dhcp_flow(hass)
    assert first["type"] is FlowResultType.ABORT
    mock_client.get_network_info.assert_awaited_once_with(TEST_DEVICE)
    mock_client.get_network_info.reset_mock()

    repeated = await _start_dhcp_flow(hass)

    assert repeated["type"] is FlowResultType.ABORT
    assert repeated["reason"] == "already_configured"
    mock_client.get_network_info.assert_not_awaited()


async def test_setup_removes_stale_dhcp_flow_after_mac_backfill(
    hass, mock_client
) -> None:
    """Test setup removes discovery started before MAC registration completed."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    discovery = await _start_dhcp_flow(hass)
    assert discovery["type"] is FlowResultType.FORM
    assert discovery["step_id"] == "dhcp_confirm"
    mock_client.get_network_info.return_value = _network_info()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert discovery["flow_id"] not in {
        flow["flow_id"]
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    }
    robot = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        entry.entry_id,
    )
    assert robot is not None
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) in robot.connections


async def test_dhcp_confirmation_rechecks_runtime_after_startup_race(
    hass, mock_client
) -> None:
    """Test confirmation catches runtime data that appeared after discovery."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    discovery = await _start_dhcp_flow(hass)
    assert discovery["type"] is FlowResultType.FORM
    assert discovery["step_id"] == "dhcp_confirm"

    mock_client.get_network_info.return_value = _network_info()
    _set_entry_runtime(hass, entry, mock_client, TEST_DEVICE)
    result = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert (
        dr.async_get(hass).async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC),
            entry.entry_id,
        )
        is not None
    )
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


async def test_existing_account_without_mac_match_remains_deduplicated(
    hass, mock_client
) -> None:
    """Test an unverified discovery aborts without changing an existing account."""

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
    mock_client.get_network_info.return_value = _network_info("00:11:22:33:44:55")
    discovery = await _start_dhcp_flow(hass)
    login = await hass.config_entries.flow.async_configure(
        discovery["flow_id"],
        {},
    )

    result = await hass.config_entries.flow.async_configure(
        login["flow_id"],
        TEST_NATIVE_INPUT,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    robot = registry.async_get_device_by_identifier(
        (DOMAIN, TEST_DEVICE.id),
        entry.entry_id,
    )
    assert robot is not None
    assert not robot.connections


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


async def test_existing_setup_does_not_merge_robots_with_duplicate_mac(
    hass, mock_client
) -> None:
    """Test an ambiguous reported MAC cannot merge two physical robots."""

    second_device = Device(
        id="second-test-device",
        name="Second Test Robot",
        model="Test Model",
        online=True,
    )
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_network_info.return_value = _network_info(DISCOVERED_MAC)
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
    assert first.id != second.id
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) not in first.connections
    assert (dr.CONNECTION_NETWORK_MAC, DISCOVERED_MAC) not in second.connections
