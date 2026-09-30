"""Tests for native per-room cleaning preference selects."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from botslab360 import ApiError, AuthenticationError, RoomCleaningSettings
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.select import ATTR_OPTION
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import (
    CONF_CLEAN_TIMES,
    CONF_FAN_MODE,
    CONF_IGNORED_ROOMS,
    CONF_ROOM_PREFERENCES,
    CONF_WATER_PUMP,
    DOMAIN,
    LEGACY_CONF_CLEANING_MODE,
)
from custom_components.botslab360.select import (
    CLEANING_MODE_MOP,
    CLEANING_MODE_SWEEP,
    CLEANING_MODE_SWEEP_AND_MOP,
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


def _room_select(hass, room_id: int, setting: str, device_id=TEST_DEVICE.id) -> str:
    return _entity_id(
        hass,
        SELECT_DOMAIN,
        f"{device_id}_room_{room_id}_{setting}",
    )


def _cleaning_mode_select(hass, device_id=TEST_DEVICE.id) -> str:
    return _entity_id(hass, SELECT_DOMAIN, f"{device_id}_cleaning_mode")


async def _select(hass, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: option},
        blocking=True,
    )


async def test_room_selects_use_robot_templates_and_stable_unique_ids(
    hass, mock_client
) -> None:
    """Test native controls initialize from valid room attributes."""

    await _setup_entry(hass)

    fan = _room_select(hass, 1, CONF_FAN_MODE)
    passes = _room_select(hass, 1, CONF_CLEAN_TIMES)
    water = _room_select(hass, 1, CONF_WATER_PUMP)
    assert hass.states.get(fan).state == "max"
    assert hass.states.get(passes).state == "2"
    assert hass.states.get(water).state == "1"
    assert er.async_get(hass).async_get(fan).unique_id == (
        f"{TEST_DEVICE.id}_room_1_fan_mode"
    )
    assert er.async_get(hass).async_get(fan).entity_category is EntityCategory.CONFIG
    assert er.async_get(hass).async_get(passes).entity_category is EntityCategory.CONFIG
    assert er.async_get(hass).async_get(water).entity_category is EntityCategory.CONFIG
    assert (
        er.async_get(hass).async_get_entity_id(
            SELECT_DOMAIN,
            DOMAIN,
            f"{TEST_DEVICE.id}_room_1_{LEGACY_CONF_CLEANING_MODE}",
        )
        is None
    )


async def test_cleaning_mode_without_wiping_assembly_allows_only_sweep(
    hass, mock_client
) -> None:
    """Test mop modes are rejected when the wiping assembly is absent."""

    mock_client.get_status.return_value = make_status(mop_status=0)
    await _setup_entry(hass)
    entity_id = _cleaning_mode_select(hass)

    assert hass.states.get(entity_id).state == CLEANING_MODE_SWEEP
    await _select(hass, entity_id, CLEANING_MODE_SWEEP)
    mock_client.set_mop_only.assert_awaited_once_with(TEST_DEVICE, False)

    with pytest.raises(HomeAssistantError) as error:
        await _select(hass, entity_id, CLEANING_MODE_MOP)
    assert error.value.translation_key == "wiping_assembly_required"


async def test_cleaning_mode_with_wiping_assembly_uses_optimistic_state(
    hass, mock_client
) -> None:
    """Test installed hardware supports both verified mop-switch states."""

    mock_client.get_status.return_value = make_status(mop_status=1)
    await _setup_entry(hass)
    entity_id = _cleaning_mode_select(hass)

    assert hass.states.get(entity_id).state == STATE_UNKNOWN
    await _select(hass, entity_id, CLEANING_MODE_SWEEP_AND_MOP)
    assert hass.states.get(entity_id).state == CLEANING_MODE_SWEEP_AND_MOP
    mock_client.set_mop_only.assert_awaited_once_with(TEST_DEVICE, False)

    mock_client.set_mop_only.reset_mock()
    await _select(hass, entity_id, CLEANING_MODE_MOP)
    assert hass.states.get(entity_id).state == CLEANING_MODE_MOP
    mock_client.set_mop_only.assert_awaited_once_with(TEST_DEVICE, True)

    with pytest.raises(HomeAssistantError) as error:
        await _select(hass, entity_id, CLEANING_MODE_SWEEP)
    assert error.value.translation_key == "remove_wiping_assembly"


async def test_cleaning_mode_discards_optimistic_state_on_hardware_change(
    hass, mock_client
) -> None:
    """Test a changed wiping assembly invalidates the local mop-only state."""

    mock_client.get_status.return_value = make_status(mop_status=1)
    entry = await _setup_entry(hass)
    entity_id = _cleaning_mode_select(hass)
    await _select(hass, entity_id, CLEANING_MODE_MOP)
    assert hass.states.get(entity_id).state == CLEANING_MODE_MOP

    entry.runtime_data.coordinator.async_set_updated_data(
        {TEST_DEVICE.id: make_status(mop_status=0)}
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == CLEANING_MODE_SWEEP

    entry.runtime_data.coordinator.async_set_updated_data(
        {TEST_DEVICE.id: make_status(mop_status=1)}
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNKNOWN


async def test_cleaning_mode_rejects_unknown_mop_status(hass, mock_client) -> None:
    """Test unknown model-dependent status values are not reinterpreted."""

    mock_client.get_status.return_value = make_status(mop_status=2)
    await _setup_entry(hass)
    entity_id = _cleaning_mode_select(hass)

    assert hass.states.get(entity_id).state == STATE_UNKNOWN
    with pytest.raises(HomeAssistantError) as error:
        await _select(hass, entity_id, CLEANING_MODE_MOP)
    assert error.value.translation_key == "mop_status_unknown"
    mock_client.set_mop_only.assert_not_awaited()


async def test_cleaning_mode_api_error_is_exposed(hass, mock_client) -> None:
    """Test command API failures use the integration's translated error."""

    mock_client.get_status.return_value = make_status(mop_status=1)
    mock_client.set_mop_only.side_effect = ApiError("synthetic failure")
    await _setup_entry(hass)

    with pytest.raises(HomeAssistantError) as error:
        await _select(hass, _cleaning_mode_select(hass), CLEANING_MODE_MOP)
    assert error.value.translation_key == "command_failed"


async def test_cleaning_mode_authentication_error_starts_reauth(
    hass, mock_client
) -> None:
    """Test command authentication failures start config-entry reauth."""

    mock_client.get_status.return_value = make_status(mop_status=1)
    mock_client.set_mop_only.side_effect = AuthenticationError("expired")
    entry = await _setup_entry(hass)

    with (
        patch.object(entry, "async_start_reauth") as start_reauth,
        pytest.raises(HomeAssistantError) as error,
    ):
        await _select(hass, _cleaning_mode_select(hass), CLEANING_MODE_MOP)
    assert error.value.translation_key == "invalid_auth"
    start_reauth.assert_called_once_with(hass)


@pytest.mark.parametrize("water_pump", [None, 0, 4])
async def test_water_select_requires_valid_template_or_preference(
    hass, mock_client, water_pump
) -> None:
    """Test no water-level control is invented for an unsupported room."""

    mock_client.get_rooms.return_value = [replace(TEST_ROOMS[0], water_pump=water_pump)]
    await _setup_entry(hass)

    assert (
        er.async_get(hass).async_get_entity_id(
            SELECT_DOMAIN,
            DOMAIN,
            f"{TEST_DEVICE.id}_room_1_water_pump",
        )
        is None
    )
    assert _room_select(hass, 1, CONF_FAN_MODE)
    assert _room_select(hass, 1, CONF_CLEAN_TIMES)


async def test_changed_preferences_persist_and_survive_reload(
    hass, mock_client
) -> None:
    """Test explicit HA preferences are stored by stable robot/room key."""

    entry = await _setup_entry(hass)
    await _select(hass, _room_select(hass, 1, CONF_FAN_MODE), "strong")
    await _select(hass, _room_select(hass, 1, CONF_CLEAN_TIMES), "1")
    await _select(hass, _room_select(hass, 1, CONF_WATER_PUMP), "3")

    assert entry.options[CONF_ROOM_PREFERENCES] == {
        f"{TEST_DEVICE.id}:1": {
            CONF_FAN_MODE: "strong",
            CONF_CLEAN_TIMES: 1,
            CONF_WATER_PUMP: 3,
        }
    }

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(_room_select(hass, 1, CONF_FAN_MODE)).state == "strong"
    assert hass.states.get(_room_select(hass, 1, CONF_CLEAN_TIMES)).state == "1"
    assert hass.states.get(_room_select(hass, 1, CONF_WATER_PUMP)).state == "3"


async def test_room_preferences_are_isolated_between_rooms(hass, mock_client) -> None:
    """Test changing one room cannot alter another room's controls."""

    entry = await _setup_entry(hass)
    await _select(hass, _room_select(hass, 1, CONF_FAN_MODE), "strong")

    assert hass.states.get(_room_select(hass, 1, CONF_FAN_MODE)).state == "strong"
    assert hass.states.get(_room_select(hass, 6, CONF_FAN_MODE)).state == "auto"
    assert set(entry.options[CONF_ROOM_PREFERENCES]) == {f"{TEST_DEVICE.id}:1"}


async def test_same_room_ids_on_different_robots_are_isolated(
    hass, mock_client
) -> None:
    """Test robot IDs namespace room preference entities and storage."""

    second_device = replace(TEST_DEVICE, id="second-test-device", name="Second Robot")
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_status.side_effect = [make_status(), make_status()]
    mock_client.get_rooms.side_effect = [[TEST_ROOMS[0]], [TEST_ROOMS[0]]]
    entry = await _setup_entry(hass)

    first = _room_select(hass, 1, CONF_FAN_MODE)
    second = _room_select(hass, 1, CONF_FAN_MODE, second_device.id)
    assert first != second
    await _select(hass, second, "quiet")
    assert hass.states.get(first).state == "max"
    assert hass.states.get(second).state == "quiet"
    assert set(entry.options[CONF_ROOM_PREFERENCES]) == {"second-test-device:1"}


async def test_clean_button_uses_current_preferred_settings(hass, mock_client) -> None:
    """Test Clean sends exactly one room with its current native settings."""

    await _setup_entry(hass)
    await _select(hass, _room_select(hass, 1, CONF_FAN_MODE), "strong")
    await _select(hass, _room_select(hass, 1, CONF_CLEAN_TIMES), "2")
    await _select(hass, _room_select(hass, 1, CONF_WATER_PUMP), "2")
    mock_client.clean_rooms.reset_mock()
    button = _entity_id(
        hass,
        BUTTON_DOMAIN,
        f"{TEST_DEVICE.id}_room_1_clean",
    )

    await hass.services.async_call(
        BUTTON_DOMAIN,
        "press",
        {ATTR_ENTITY_ID: button},
        blocking=True,
    )

    mock_client.clean_rooms.assert_awaited_once_with(
        TEST_DEVICE,
        [1],
        room_settings={
            1: RoomCleaningSettings(
                fan_mode="strong",
                clean_times=2,
                water_pump=2,
            )
        },
    )


async def test_legacy_cleaning_mode_preference_and_entity_are_removed(
    hass, mock_client
) -> None:
    """Test 0.2.4 mode state is removed and never reaches room cleaning."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=TEST_CREDENTIALS,
        options={
            CONF_ROOM_PREFERENCES: {
                f"{TEST_DEVICE.id}:1": {
                    LEGACY_CONF_CLEANING_MODE: 2,
                    CONF_FAN_MODE: "strong",
                }
            }
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        SELECT_DOMAIN,
        DOMAIN,
        f"{TEST_DEVICE.id}_room_1_{LEGACY_CONF_CLEANING_MODE}",
        config_entry=entry,
    )
    retained_fan = registry.async_get_or_create(
        SELECT_DOMAIN,
        DOMAIN,
        f"{TEST_DEVICE.id}_room_1_{CONF_FAN_MODE}",
        config_entry=entry,
        suggested_object_id="retained_room_fan",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(legacy.entity_id) is None
    assert registry.async_get(retained_fan.entity_id) is not None
    assert entry.options[CONF_ROOM_PREFERENCES] == {
        f"{TEST_DEVICE.id}:1": {CONF_FAN_MODE: "strong"}
    }

    mock_client.clean_rooms.reset_mock()
    button = _entity_id(
        hass,
        BUTTON_DOMAIN,
        f"{TEST_DEVICE.id}_room_1_clean",
    )
    await hass.services.async_call(
        BUTTON_DOMAIN,
        "press",
        {ATTR_ENTITY_ID: button},
        blocking=True,
    )

    mock_client.clean_rooms.assert_awaited_once_with(
        TEST_DEVICE,
        [1],
        room_settings={
            1: RoomCleaningSettings(
                fan_mode="strong",
                clean_times=2,
                water_pump=1,
            )
        },
    )


async def test_ignored_room_exposes_no_selects(hass, mock_client) -> None:
    """Test ignored rooms have neither cleaning buttons nor setting controls."""

    await _setup_entry(
        hass,
        options={CONF_IGNORED_ROOMS: [f"{TEST_DEVICE.id}:1"]},
    )

    registry = er.async_get(hass)
    for setting in (
        CONF_FAN_MODE,
        CONF_CLEAN_TIMES,
        CONF_WATER_PUMP,
    ):
        assert (
            registry.async_get_entity_id(
                SELECT_DOMAIN,
                DOMAIN,
                f"{TEST_DEVICE.id}_room_1_{setting}",
            )
            is None
        )
