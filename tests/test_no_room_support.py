"""Robots without room segmentation retain normal Home Assistant functionality."""

import logging
from dataclasses import replace

import pytest
from botslab360 import ApiError, AuthenticationError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import DOMAIN

from .conftest import (
    TEST_ACCOUNT_FINGERPRINT,
    TEST_DEVICE,
    TEST_NATIVE_ENTRY_DATA,
    TEST_ROOMS,
    make_status,
)
from .test_config_flow import _submit_room_areas, _submit_user_flow


@pytest.mark.parametrize("mixed_account", [False, True])
async def test_no_room_robot_can_finish_flow_and_load(
    hass, mock_client, caplog, mixed_account
):
    """An empty public room result never suppresses the robot or another robot's rooms."""

    caplog.set_level(logging.DEBUG, logger="custom_components.botslab360.config_flow")
    room_robot = replace(TEST_DEVICE, id="room-capable-device", name="Room Robot")
    devices = [TEST_DEVICE, room_robot] if mixed_account else [TEST_DEVICE]
    mock_client.get_devices.return_value = devices
    mock_client.get_rooms.side_effect = lambda device: (
        [] if device.id == TEST_DEVICE.id else TEST_ROOMS
    )
    mock_client.get_status.side_effect = lambda device_id, **_kwargs: replace(
        make_status(), device_id=device_id
    )

    rooms_form = await _submit_user_flow(hass)
    assert rooms_form["step_id"] == "room_areas"
    assert len(rooms_form["data_schema"].schema) == (4 if mixed_account else 0)
    assert any(
        "stage=rooms result=succeeded" in record.getMessage()
        and "room_count=0 device_index=1" in record.getMessage()
        for record in caplog.records
    )
    result = await _submit_room_areas(hass, rooms_form)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is mock_client
    assert set(entry.runtime_data.coordinator.data) == {device.id for device in devices}

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    for device in devices:
        vacuum_id = entity_registry.async_get_entity_id("vacuum", DOMAIN, device.id)
        assert vacuum_id is not None
        assert hass.states.get(vacuum_id).state == "idle"
        for key, expected in (
            ("battery", "73"),
            ("cleaned_area", "42"),
            ("cleaning_time", "321"),
            ("error_code", "0"),
        ):
            entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, f"{device.id}_{key}"
            )
            assert entity_id is not None
            assert hass.states.get(entity_id).state == expected
        parent_id = dr.async_get_device_id_by_identifier(
            hass, (DOMAIN, device.id), config_entry_id=entry.entry_id
        )
        assert parent_id is not None
        children = dr.async_entries_for_parent_device(device_registry, parent_id)
        assert len(children) == (len(TEST_ROOMS) if device.id == room_robot.id else 0)

    entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    assert not any(
        entity.unique_id.startswith(f"{TEST_DEVICE.id}_room_") for entity in entities
    )
    assert len(entry.runtime_data.rooms) == (len(TEST_ROOMS) if mixed_account else 0)
    if mixed_account:
        assert {
            prepared.discovered_room.device.id for prepared in entry.runtime_data.rooms
        } == {room_robot.id}
        for room in TEST_ROOMS:
            button_id = entity_registry.async_get_entity_id(
                "button", DOMAIN, f"{room_robot.id}_room_{room.id}_clean"
            )
            assert button_id is not None
            assert hass.states.get(button_id) is not None
    mock_client.authenticate.assert_awaited_once()
    for device in devices:
        assert (
            sum(
                call.args == (device,) for call in mock_client.get_rooms.await_args_list
            )
            == 2
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("synthetic auth failure"), "invalid_auth"),
        (ApiError("synthetic transport failure", phase="transport"), "cannot_connect"),
        (TimeoutError("synthetic timeout"), "cannot_connect"),
    ],
)
async def test_room_failures_still_prevent_config_flow(
    hass, mock_client, error, expected
):
    """Only a successful [] result is accepted; failed room discovery remains fatal."""

    mock_client.get_rooms.side_effect = error
    result = await _submit_user_flow(hass)
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected}
    assert not hass.config_entries.async_entries(DOMAIN)
    mock_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError("synthetic auth failure"),
        ApiError("synthetic transport failure", phase="transport"),
        TimeoutError("synthetic timeout"),
    ],
)
async def test_room_failures_still_prevent_entry_setup(hass, mock_client, error):
    """Setup does not misclassify authentication or transport failures as no rooms."""

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=TEST_ACCOUNT_FINGERPRINT, data=TEST_NATIVE_ENTRY_DATA
    )
    entry.add_to_hass(hass)
    mock_client.get_rooms.side_effect = error
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is not ConfigEntryState.LOADED
    assert not hass.states.async_all("vacuum")
    mock_client.close.assert_awaited_once()
