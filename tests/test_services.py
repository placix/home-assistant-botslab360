"""Tests for Botslab 360 integration actions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol

from botslab360 import ApiError, AuthenticationError, RoomCleaningSettings
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import (
    CONF_CLEAN_TIMES,
    CONF_FAN_MODE,
    CONF_ROOM_IDS,
    CONF_WATER_PUMP,
    DOMAIN,
    SERVICE_CLEAN_ROOMS,
)

from .conftest import TEST_CREDENTIALS, TEST_DEVICE


async def _setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize("room_ids", [[1], [1, 6]])
async def test_clean_rooms_action_forwards_selected_ids(
    hass, mock_client, room_ids
) -> None:
    """Test one or multiple room IDs use the public client API unchanged."""

    await _setup_entry(hass)
    mock_client.get_status.reset_mock()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CLEAN_ROOMS,
        {CONF_ROOM_IDS: room_ids},
        target={ATTR_ENTITY_ID: "vacuum.test_robot"},
        blocking=True,
    )

    mock_client.clean_rooms.assert_awaited_once_with(
        TEST_DEVICE,
        room_ids,
        room_settings=None,
    )
    mock_client.get_status.assert_awaited_once_with(TEST_DEVICE.id)


async def test_clean_rooms_action_forwards_confirmed_room_settings(
    hass, mock_client
) -> None:
    """Test repeats, fan mode, and water level become per-room settings."""

    await _setup_entry(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CLEAN_ROOMS,
        {
            ATTR_ENTITY_ID: "vacuum.test_robot",
            CONF_ROOM_IDS: [1, 6],
            CONF_CLEAN_TIMES: 2,
            CONF_FAN_MODE: "max",
            CONF_WATER_PUMP: 3,
        },
        blocking=True,
    )

    expected = RoomCleaningSettings(
        clean_times=2,
        fan_mode="max",
        water_pump=3,
    )
    mock_client.clean_rooms.assert_awaited_once_with(
        TEST_DEVICE,
        [1, 6],
        room_settings={1: expected, 6: expected},
    )


@pytest.mark.parametrize(
    "room_ids",
    [[], [True], ["1"]],
)
async def test_clean_rooms_action_rejects_invalid_room_ids(
    hass, mock_client, room_ids
) -> None:
    """Test the service schema rejects empty and non-integer room lists."""

    await _setup_entry(hass)

    with pytest.raises((ServiceValidationError, vol.Invalid)):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAN_ROOMS,
            {ATTR_ENTITY_ID: "vacuum.test_robot", CONF_ROOM_IDS: room_ids},
            blocking=True,
        )
    mock_client.clean_rooms.assert_not_awaited()


async def test_clean_rooms_action_rejects_wrong_entity_target(
    hass, mock_client
) -> None:
    """Test a non-Botslab-vacuum target is rejected."""

    await _setup_entry(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAN_ROOMS,
            {
                ATTR_ENTITY_ID: "sensor.test_robot_battery",
                CONF_ROOM_IDS: [1],
            },
            blocking=True,
        )
    mock_client.clean_rooms.assert_not_awaited()


async def test_clean_rooms_action_rejects_multiple_targets(
    hass, mock_client
) -> None:
    """Test the action enforces exactly one vacuum entity target."""

    await _setup_entry(hass)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAN_ROOMS,
            {
                ATTR_ENTITY_ID: [
                    "vacuum.test_robot",
                    "vacuum.second_robot",
                ],
                CONF_ROOM_IDS: [1],
            },
            blocking=True,
        )
    mock_client.clean_rooms.assert_not_awaited()


async def test_clean_rooms_action_rejects_unloaded_target(
    hass, mock_client
) -> None:
    """Test a registry entry without loaded runtime data is rejected."""

    entry = await _setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAN_ROOMS,
            {ATTR_ENTITY_ID: "vacuum.test_robot", CONF_ROOM_IDS: [1]},
            blocking=True,
        )
    mock_client.clean_rooms.assert_not_awaited()


async def test_clean_rooms_authentication_error_starts_reauth(
    hass, mock_client
) -> None:
    """Test authentication failure starts Home Assistant reauthentication."""

    entry = await _setup_entry(hass)
    mock_client.clean_rooms.side_effect = AuthenticationError("expired")
    with patch.object(entry, "async_start_reauth") as start_reauth:
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_CLEAN_ROOMS,
                {ATTR_ENTITY_ID: "vacuum.test_robot", CONF_ROOM_IDS: [1]},
                blocking=True,
            )

    start_reauth.assert_called_once_with(hass)


async def test_clean_rooms_api_error_becomes_home_assistant_error(
    hass, mock_client
) -> None:
    """Test library API errors are translated for action callers."""

    await _setup_entry(hass)
    mock_client.clean_rooms.side_effect = ApiError("rejected")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAN_ROOMS,
            {ATTR_ENTITY_ID: "vacuum.test_robot", CONF_ROOM_IDS: [1]},
            blocking=True,
        )
