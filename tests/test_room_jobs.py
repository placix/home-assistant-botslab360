"""Tests for native temporary multi-room cleaning jobs."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from botslab360 import ApiError, AuthenticationError, RoomCleaningSettings
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import (
    CONF_CLEAN_TIMES,
    CONF_FAN_MODE,
    CONF_IGNORED_ROOMS,
    CONF_ROOM_PREFERENCES,
    CONF_WATER_PUMP,
    DOMAIN,
)

from .conftest import TEST_CREDENTIALS, TEST_DEVICE, TEST_ROOMS, make_status


async def _setup_entry(hass, *, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=TEST_CREDENTIALS,
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def _job_switch(hass, room_id: int, device_id: str = TEST_DEVICE.id) -> str:
    return _entity_id(hass, SWITCH_DOMAIN, f"{device_id}_job_room_{room_id}")


def _job_button(hass, action: str, device_id: str = TEST_DEVICE.id) -> str:
    return _entity_id(hass, BUTTON_DOMAIN, f"{device_id}_job_{action}")


async def _turn_on(hass, entity_id: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )


async def _press(hass, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN,
        "press",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )


async def test_job_controls_use_physical_robot_and_stable_unique_ids(
    hass, mock_client
) -> None:
    """Test every active room has one physical-device job switch."""

    entry = await _setup_entry(hass)
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    parent_id = dr.async_get_device_id_by_identifier(
        hass,
        (DOMAIN, TEST_DEVICE.id),
        config_entry_id=entry.entry_id,
    )
    assert parent_id is not None

    first = _job_switch(hass, 1)
    second = _job_switch(hass, 6)
    assert first != second
    assert registry.async_get(first).unique_id == f"{TEST_DEVICE.id}_job_room_1"
    assert registry.async_get(second).unique_id == f"{TEST_DEVICE.id}_job_room_6"
    assert registry.async_get(first).device_id == parent_id
    assert registry.async_get(second).device_id == parent_id
    assert hass.states.get(first).attributes["icon"] == (
        "mdi:checkbox-marked-circle-outline"
    )

    start = _job_button(hass, "start")
    clear = _job_button(hass, "clear")
    assert registry.async_get(start).device_id == parent_id
    assert registry.async_get(clear).device_id == parent_id
    assert registry.async_get(start).unique_id == f"{TEST_DEVICE.id}_job_start"
    assert registry.async_get(clear).unique_id == f"{TEST_DEVICE.id}_job_clear"

    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, f"{TEST_DEVICE.id}_room_1"),
        entry.entry_id,
    )
    room_button = _entity_id(
        hass,
        BUTTON_DOMAIN,
        f"{TEST_DEVICE.id}_room_1_clean",
    )
    assert child is not None
    assert registry.async_get(room_button).device_id == child.id


async def test_switch_selection_is_local_and_isolated_between_rooms(
    hass, mock_client
) -> None:
    """Test toggling a room only changes HA-side temporary state."""

    await _setup_entry(hass)
    first = _job_switch(hass, 1)
    second = _job_switch(hass, 6)
    mock_client.clean_rooms.reset_mock()

    await _turn_on(hass, first)

    assert hass.states.get(first).state == STATE_ON
    assert hass.states.get(second).state == STATE_OFF
    mock_client.clean_rooms.assert_not_awaited()


async def test_start_without_selection_raises_translated_error(
    hass, mock_client
) -> None:
    """Test an empty job never sends a robot command."""

    await _setup_entry(hass)
    mock_client.clean_rooms.reset_mock()

    with pytest.raises(HomeAssistantError) as error:
        await _press(hass, _job_button(hass, "start"))

    assert error.value.translation_key == "no_rooms_selected"
    mock_client.clean_rooms.assert_not_awaited()


async def test_one_selected_room_sends_one_request_and_clears_selection(
    hass, mock_client
) -> None:
    """Test a one-room job uses the same public multi-room API once."""

    await _setup_entry(hass)
    selected = _job_switch(hass, 1)
    await _turn_on(hass, selected)
    mock_client.clean_rooms.reset_mock()

    await _press(hass, _job_button(hass, "start"))

    mock_client.clean_rooms.assert_awaited_once_with(
        TEST_DEVICE,
        [1],
        room_settings={
            1: RoomCleaningSettings(
                clean_times=2,
                fan_mode="max",
                water_pump=1,
            )
        },
    )
    assert hass.states.get(selected).state == STATE_OFF


async def test_multi_room_job_preserves_individual_preferences(
    hass, mock_client
) -> None:
    """Test one request carries independent settings for every selected room."""

    await _setup_entry(
        hass,
        options={
            CONF_ROOM_PREFERENCES: {
                f"{TEST_DEVICE.id}:1": {
                    CONF_FAN_MODE: "strong",
                    CONF_CLEAN_TIMES: 2,
                    CONF_WATER_PUMP: 2,
                },
                f"{TEST_DEVICE.id}:6": {
                    CONF_FAN_MODE: "quiet",
                    CONF_CLEAN_TIMES: 1,
                    CONF_WATER_PUMP: 3,
                },
            }
        },
    )
    first = _job_switch(hass, 1)
    second = _job_switch(hass, 6)
    await _turn_on(hass, first)
    await _turn_on(hass, second)
    mock_client.clean_rooms.reset_mock()

    await _press(hass, _job_button(hass, "start"))

    mock_client.clean_rooms.assert_awaited_once_with(
        TEST_DEVICE,
        [1, 6],
        room_settings={
            1: RoomCleaningSettings(
                fan_mode="strong",
                clean_times=2,
                water_pump=2,
            ),
            6: RoomCleaningSettings(
                fan_mode="quiet",
                clean_times=1,
                water_pump=3,
            ),
        },
    )
    assert hass.states.get(first).state == STATE_OFF
    assert hass.states.get(second).state == STATE_OFF


async def test_failed_job_keeps_selection(hass, mock_client) -> None:
    """Test a rejected robot command does not silently discard the job."""

    await _setup_entry(hass)
    selected = _job_switch(hass, 1)
    await _turn_on(hass, selected)
    mock_client.clean_rooms.side_effect = ApiError("rejected")

    with pytest.raises(HomeAssistantError):
        await _press(hass, _job_button(hass, "start"))

    assert hass.states.get(selected).state == STATE_ON


async def test_job_authentication_error_starts_reauth_and_keeps_selection(
    hass, mock_client
) -> None:
    """Test central job commands use the shared authentication error handling."""

    entry = await _setup_entry(hass)
    selected = _job_switch(hass, 1)
    await _turn_on(hass, selected)
    mock_client.clean_rooms.side_effect = AuthenticationError("expired")

    with (
        patch.object(entry, "async_start_reauth") as start_reauth,
        pytest.raises(HomeAssistantError),
    ):
        await _press(hass, _job_button(hass, "start"))

    start_reauth.assert_called_once_with(hass)
    assert hass.states.get(selected).state == STATE_ON


async def test_disappeared_selected_room_is_removed_before_start(
    hass, mock_client
) -> None:
    """Test stale selected rooms are discarded instead of sent to the robot."""

    entry = await _setup_entry(hass)
    selected = _job_switch(hass, 1)
    await _turn_on(hass, selected)
    entry.runtime_data.rooms = tuple(
        prepared
        for prepared in entry.runtime_data.rooms
        if prepared.discovered_room.room.id != 1
    )
    mock_client.clean_rooms.reset_mock()

    with pytest.raises(HomeAssistantError) as error:
        await _press(hass, _job_button(hass, "start"))

    assert error.value.translation_key == "no_rooms_selected"
    assert hass.states.get(selected).state == STATE_OFF
    mock_client.clean_rooms.assert_not_awaited()


async def test_clear_selection_performs_no_robot_command(hass, mock_client) -> None:
    """Test the central clear button only resets HA-side state."""

    await _setup_entry(hass)
    first = _job_switch(hass, 1)
    second = _job_switch(hass, 6)
    await _turn_on(hass, first)
    await _turn_on(hass, second)
    mock_client.clean_rooms.reset_mock()

    await _press(hass, _job_button(hass, "clear"))

    assert hass.states.get(first).state == STATE_OFF
    assert hass.states.get(second).state == STATE_OFF
    mock_client.clean_rooms.assert_not_awaited()


async def test_selections_are_isolated_between_robots(hass, mock_client) -> None:
    """Test starting one robot cannot consume another robot's selection."""

    second_device = replace(TEST_DEVICE, id="second-test-device", name="Second Robot")
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_status.side_effect = lambda _device_id: make_status()
    mock_client.get_rooms.side_effect = [[TEST_ROOMS[0]], [TEST_ROOMS[0]]]
    await _setup_entry(hass)
    first = _job_switch(hass, 1)
    second = _job_switch(hass, 1, second_device.id)
    await _turn_on(hass, first)
    await _turn_on(hass, second)
    mock_client.clean_rooms.reset_mock()

    await _press(hass, _job_button(hass, "start"))

    mock_client.clean_rooms.assert_awaited_once()
    assert mock_client.clean_rooms.await_args.args[:2] == (TEST_DEVICE, [1])
    assert hass.states.get(first).state == STATE_OFF
    assert hass.states.get(second).state == STATE_ON


async def test_ignored_room_switch_is_removed_and_unignore_restores_it_off(
    hass, mock_client
) -> None:
    """Test ignore changes clean registry state and reset temporary selection."""

    entry = await _setup_entry(hass)
    unique_id = f"{TEST_DEVICE.id}_job_room_1"
    selected = _job_switch(hass, 1)
    await _turn_on(hass, selected)

    hass.config_entries.async_update_entry(
        entry,
        options={CONF_IGNORED_ROOMS: [f"{TEST_DEVICE.id}:1"]},
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert (
        er.async_get(hass).async_get_entity_id(SWITCH_DOMAIN, DOMAIN, unique_id) is None
    )
    assert _job_switch(hass, 6)

    hass.config_entries.async_update_entry(entry, options={CONF_IGNORED_ROOMS: []})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    restored = _job_switch(hass, 1)
    assert er.async_get(hass).async_get(restored).unique_id == unique_id
    assert hass.states.get(restored).state == STATE_OFF
