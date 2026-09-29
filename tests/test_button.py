"""Tests for Botslab 360 room-cleaning buttons."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from botslab360 import ApiError, AuthenticationError
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import DOMAIN

from .conftest import TEST_CREDENTIALS, TEST_DEVICE, TEST_ROOMS, make_status


async def _setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _room_button_entity_id(hass, device_id: str, room_id: int) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        BUTTON_DOMAIN,
        DOMAIN,
        f"{device_id}_room_{room_id}_clean",
    )
    assert entity_id is not None
    return entity_id


async def test_one_room_creates_one_button(hass, mock_client) -> None:
    """Test one reported room creates one native button entity."""

    mock_client.get_rooms.return_value = [TEST_ROOMS[0]]
    await _setup_entry(hass)

    entity_id = _room_button_entity_id(hass, TEST_DEVICE.id, 1)
    state = hass.states.get(entity_id)
    registry_entry = er.async_get(hass).async_get(entity_id)

    assert state is not None
    assert state.attributes["icon"] == "mdi:broom"
    assert registry_entry is not None
    assert registry_entry.original_name == "Clean Bad"
    assert len(hass.states.async_all(BUTTON_DOMAIN)) == 1
    mock_client.get_rooms.assert_awaited_once_with(TEST_DEVICE)


async def test_multiple_rooms_create_separate_buttons(hass, mock_client) -> None:
    """Test every reported room receives its own stable button."""

    await _setup_entry(hass)

    first = _room_button_entity_id(hass, TEST_DEVICE.id, 1)
    second = _room_button_entity_id(hass, TEST_DEVICE.id, 6)

    assert first != second
    assert hass.states.get(first) is not None
    assert hass.states.get(second) is not None
    assert len(hass.states.async_all(BUTTON_DOMAIN)) == 2


async def test_press_cleans_exactly_the_associated_room(
    hass, mock_client
) -> None:
    """Test pressing a button delegates one room ID to the public API."""

    await _setup_entry(hass)
    mock_client.get_status.reset_mock()
    entity_id = _room_button_entity_id(hass, TEST_DEVICE.id, 1)

    await hass.services.async_call(
        BUTTON_DOMAIN,
        "press",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )

    mock_client.clean_rooms.assert_awaited_once_with(TEST_DEVICE, [1])
    mock_client.get_status.assert_awaited_once_with(TEST_DEVICE.id)


async def test_same_room_id_on_two_robots_has_distinct_unique_ids(
    hass, mock_client
) -> None:
    """Test robot IDs namespace otherwise identical room IDs."""

    second_device = TEST_DEVICE.__class__(
        id="second-test-device",
        name="Second Robot",
        model="Test Model",
        online=True,
    )
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_status.side_effect = [make_status(), make_status()]
    mock_client.get_rooms.side_effect = [
        [TEST_ROOMS[0]],
        [TEST_ROOMS[0]],
    ]

    await _setup_entry(hass)

    first = _room_button_entity_id(hass, TEST_DEVICE.id, 1)
    second = _room_button_entity_id(hass, second_device.id, 1)
    registry = er.async_get(hass)

    assert first != second
    assert registry.async_get(first).unique_id == f"{TEST_DEVICE.id}_room_1_clean"
    assert registry.async_get(second).unique_id == "second-test-device_room_1_clean"


async def test_room_button_api_error_becomes_home_assistant_error(
    hass, mock_client
) -> None:
    """Test room-cleaning API failures are exposed to button callers."""

    await _setup_entry(hass)
    mock_client.clean_rooms.side_effect = ApiError("rejected")
    entity_id = _room_button_entity_id(hass, TEST_DEVICE.id, 1)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            BUTTON_DOMAIN,
            "press",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )


async def test_room_button_authentication_error_starts_reauth(
    hass, mock_client
) -> None:
    """Test authentication failures start config-entry reauthentication."""

    entry = await _setup_entry(hass)
    mock_client.clean_rooms.side_effect = AuthenticationError("expired")
    entity_id = _room_button_entity_id(hass, TEST_DEVICE.id, 1)

    with (
        patch.object(entry, "async_start_reauth") as start_reauth,
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            BUTTON_DOMAIN,
            "press",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    start_reauth.assert_called_once_with(hass)
