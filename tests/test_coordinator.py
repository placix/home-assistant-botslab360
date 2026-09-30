"""Tests for the Botslab 360 coordinator and entry lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botslab360 import (
    ApiError,
    AuthBackend,
    AuthenticationError,
    CaptchaChallenge,
    CaptchaRequired,
    QihooCredentials,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360 import (
    Botslab360RuntimeData,
    create_client_from_entry_data,
)
from custom_components.botslab360.const import (
    CONF_CACHED_Q,
    CONF_CACHED_T,
    DOMAIN,
    PLATFORMS,
)
from custom_components.botslab360.coordinator import Botslab360Coordinator

from .conftest import (
    TEST_CACHED_CREDENTIALS,
    TEST_CREDENTIALS,
    TEST_DEVICE,
    TEST_NATIVE_ENTRY_DATA,
    make_mock_client,
    make_status,
)


def _entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data=TEST_CREDENTIALS)


async def test_coordinator_updates_all_discovered_devices(hass, mock_client) -> None:
    """Test discovery and polling of every robot."""

    second_device = TEST_DEVICE.__class__(
        id="second-test-device",
        name="Second Robot",
        model="Test Model",
        online=True,
    )
    second_status = make_status(state="sweep")
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_status.side_effect = [make_status(), second_status]
    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)

    await coordinator._async_setup()
    await coordinator.async_refresh()

    assert coordinator.update_interval == timedelta(seconds=60)
    mock_client.get_devices.assert_awaited_once()
    assert set(coordinator.devices) == {TEST_DEVICE.id, second_device.id}
    assert set(coordinator.data) == {TEST_DEVICE.id, second_device.id}
    assert mock_client.get_status.await_args_list[0].args == (TEST_DEVICE.id,)
    assert mock_client.get_status.await_args_list[1].args == (second_device.id,)
    mock_client.get_rooms.assert_not_awaited()


def test_legacy_entry_uses_q_t_client_constructor() -> None:
    """Test existing Q/T entries keep using the legacy public API."""

    with patch("custom_components.botslab360.Botslab360Client") as client_class:
        client = create_client_from_entry_data(TEST_CREDENTIALS)

    assert client is client_class.return_value
    client_class.assert_called_once_with(
        TEST_CREDENTIALS["q"],
        TEST_CREDENTIALS["t"],
    )
    client_class.from_credentials.assert_not_called()


def test_native_entry_uses_saved_backend_and_identity() -> None:
    """Test native entries reconstruct their credential client exactly."""

    with patch("custom_components.botslab360.Botslab360Client") as client_class:
        client = create_client_from_entry_data(TEST_NATIVE_ENTRY_DATA)

    assert client is client_class.from_credentials.return_value
    client_class.assert_not_called()
    call = client_class.from_credentials.call_args
    assert call.kwargs["email"] == TEST_NATIVE_ENTRY_DATA["email"]
    assert call.kwargs["password"] == TEST_NATIVE_ENTRY_DATA["password"]
    assert call.kwargs["backend"] is AuthBackend.ROBOT360
    assert call.kwargs["device_identity"].mid == ("0123456789abcdef0123456789abcdef")
    assert call.kwargs["device_identity"].android_id == ("0123456789abcdef")
    assert call.kwargs["device_identity"].m2 == ("00000000-0000-4000-8000-000000000001")
    assert "region" not in call.kwargs


def test_native_entry_prefers_cached_q_t_client() -> None:
    """Test cached native credentials remain distinct from legacy entry data."""

    data = {
        **TEST_NATIVE_ENTRY_DATA,
        CONF_CACHED_Q: TEST_CACHED_CREDENTIALS.q,
        CONF_CACHED_T: TEST_CACHED_CREDENTIALS.t,
    }
    with patch("custom_components.botslab360.Botslab360Client") as client_class:
        client = create_client_from_entry_data(data)

    assert client is client_class.return_value
    client_class.assert_called_once_with(
        TEST_CACHED_CREDENTIALS.q,
        TEST_CACHED_CREDENTIALS.t,
    )
    client_class.from_credentials.assert_not_called()


async def test_coordinator_translates_api_error(hass, mock_client) -> None:
    """Test an API error becomes an UpdateFailed error."""

    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    mock_client.get_status.side_effect = ApiError("unavailable")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_coordinator_translates_authentication_error(hass, mock_client) -> None:
    """Test an authentication error triggers Home Assistant reauth."""

    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    mock_client.get_status.side_effect = AuthenticationError("expired")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_entry_runtime_data_and_unload(hass, mock_client) -> None:
    """Test the client and coordinator live on entry.runtime_data."""

    entry = _entry()
    entry.add_to_hass(hass)
    forward_entry_setups = hass.config_entries.async_forward_entry_setups
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        new=AsyncMock(wraps=forward_entry_setups),
    ) as forward_mock:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert isinstance(entry.runtime_data, Botslab360RuntimeData)
        assert entry.runtime_data.client is mock_client
        assert entry.runtime_data.coordinator.data[TEST_DEVICE.id] == make_status()
        forward_mock.assert_awaited_once_with(entry, PLATFORMS)
        assert hass.states.get("sensor.test_robot_error_code") is not None
        assert hass.states.get("sensor.test_robot_fan_mode") is None
        fan_mode = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{TEST_DEVICE.id}_fan_mode"
        )
        assert fan_mode is not None
        assert (
            er.async_get(hass).async_get(fan_mode).disabled_by
            is RegistryEntryDisabler.INTEGRATION
        )
        mock_client.authenticate.assert_awaited_once()

        clear_map_cache = MagicMock()
        entry.runtime_data.map_cache.clear = clear_map_cache
        assert await hass.config_entries.async_unload(entry.entry_id)

    clear_map_cache.assert_called_once_with()
    mock_client.close.assert_awaited_once()


async def test_native_entry_without_handoff_authenticates_after_restart(
    hass, mock_client
) -> None:
    """Test native entries reconstruct and authenticate without a handoff."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-account-fingerprint",
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.botslab360.create_client_from_entry_data",
        return_value=mock_client,
    ) as factory:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.runtime_data.client is mock_client
    factory.assert_called_once_with(TEST_NATIVE_ENTRY_DATA)
    mock_client.authenticate.assert_awaited_once()
    mock_client.get_devices.assert_awaited_once()

    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_client.close.assert_awaited_once()


async def test_native_restart_uses_cached_q_t_without_quc_login(hass, caplog) -> None:
    """Test a restart establishes a fresh session directly from cached Q/T."""

    cached_client = make_mock_client()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-account-fingerprint",
        data={
            **TEST_NATIVE_ENTRY_DATA,
            CONF_CACHED_Q: TEST_CACHED_CREDENTIALS.q,
            CONF_CACHED_T: TEST_CACHED_CREDENTIALS.t,
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.botslab360.Botslab360Client") as client_class:
        client_class.return_value = cached_client
        client_class.from_credentials.side_effect = AssertionError(
            "native QUC login must not run with valid cached Q/T"
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is cached_client
    client_class.assert_called_once_with(
        TEST_CACHED_CREDENTIALS.q,
        TEST_CACHED_CREDENTIALS.t,
    )
    client_class.from_credentials.assert_not_called()
    cached_client.authenticate.assert_awaited_once()
    assert TEST_CACHED_CREDENTIALS.q not in caplog.text
    assert TEST_CACHED_CREDENTIALS.t not in caplog.text

    assert await hass.config_entries.async_unload(entry.entry_id)
    cached_client.close.assert_awaited_once()


async def test_invalid_native_cache_falls_back_and_refreshes_q_t(
    hass,
) -> None:
    """Test rejected cached Q/T falls back once to stored native credentials."""

    cached_client = make_mock_client()
    cached_client.authenticate.side_effect = AuthenticationError("expired cache")
    native_client = make_mock_client()
    refreshed = QihooCredentials(
        q="u=360H1234567890&n=synthetic&m=refreshed-token",
        t="s=refreshed-session&t=1700000001&v=2.0",
        qid="1234567890",
    )
    native_client.credentials = refreshed
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-account-fingerprint",
        data={
            **TEST_NATIVE_ENTRY_DATA,
            CONF_CACHED_Q: TEST_CACHED_CREDENTIALS.q,
            CONF_CACHED_T: TEST_CACHED_CREDENTIALS.t,
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.botslab360.Botslab360Client") as client_class:
        client_class.return_value = cached_client
        client_class.from_credentials.return_value = native_client
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is native_client
    cached_client.authenticate.assert_awaited_once()
    cached_client.close.assert_awaited_once()
    native_client.authenticate.assert_awaited_once()
    assert entry.data[CONF_CACHED_Q] == refreshed.q
    assert entry.data[CONF_CACHED_T] == refreshed.t
    call = client_class.from_credentials.call_args
    assert (
        call.kwargs["device_identity"].mid
        == (TEST_NATIVE_ENTRY_DATA["device_identity"]["mid"])
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    native_client.close.assert_awaited_once()


async def test_cached_q_t_fallback_captcha_starts_reauthentication(hass) -> None:
    """Test interactive fallback authentication uses the existing reauth flow."""

    cached_client = make_mock_client()
    cached_client.authenticate.side_effect = AuthenticationError("expired cache")
    native_client = make_mock_client()
    native_client.authenticate.side_effect = CaptchaRequired(
        CaptchaChallenge(image=b"synthetic-image", sc="synthetic-context")
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-account-fingerprint",
        data={
            **TEST_NATIVE_ENTRY_DATA,
            CONF_CACHED_Q: TEST_CACHED_CREDENTIALS.q,
            CONF_CACHED_T: TEST_CACHED_CREDENTIALS.t,
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.botslab360.Botslab360Client") as client_class:
        client_class.return_value = cached_client
        client_class.from_credentials.return_value = native_client
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert cached_client.close.await_count == 1
    assert native_client.close.await_count == 1
    assert any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )
