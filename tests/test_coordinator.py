"""Tests for the Botslab 360 coordinator and entry lifecycle."""

import asyncio
import logging
from asyncio import CancelledError, Event
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

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
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from httpx import ReadError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

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

COORDINATOR_LOGGER = "custom_components.botslab360.coordinator"
DIAGNOSTIC_LOGGER = "custom_components.botslab360.diagnostics"


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
    assert mock_client.get_status.await_args_list == [
        call(TEST_DEVICE.id, total_timeout=125.0),
        call(second_device.id, total_timeout=125.0),
    ]
    mock_client.get_rooms.assert_not_awaited()


async def test_status_refresh_preserves_availability_until_final_outcome(
    hass, mock_client, caplog
) -> None:
    """Publish final results only, then recover on a later coordinator update."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    entity_id = "sensor.test_robot_battery"
    original = coordinator.data[TEST_DEVICE.id]
    entered = Event()
    finish = Event()
    recovered = replace(make_status(), battery=71)

    async def recovering(device_id, *, total_timeout):
        assert (device_id, total_timeout) == (TEST_DEVICE.id, 125.0)
        entered.set()
        await finish.wait()
        return recovered

    mock_client.get_status.reset_mock()
    mock_client.get_status.side_effect = recovering
    refresh = asyncio.create_task(coordinator.async_request_refresh())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert coordinator.last_update_success
        assert coordinator.data[TEST_DEVICE.id] is original
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
    finally:
        finish.set()
        await refresh
    mock_client.get_status.assert_awaited_once_with(TEST_DEVICE.id, total_timeout=125.0)
    assert coordinator.data[TEST_DEVICE.id] is recovered
    assert hass.states.get(entity_id).state == "71"

    exhausted = ApiError(
        "Status retrieval exceeded its total time budget", phase="timeout"
    )
    mock_client.get_status.reset_mock()
    mock_client.get_status.side_effect = exhausted
    caplog.set_level(logging.DEBUG, logger=DIAGNOSTIC_LOGGER)
    await coordinator.async_refresh()
    mock_client.get_status.assert_awaited_once_with(TEST_DEVICE.id, total_timeout=125.0)
    assert not coordinator.last_update_success
    assert coordinator.data[TEST_DEVICE.id] is recovered
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert "message=Status retrieval exceeded its total time budget" in caplog.text

    latest = replace(make_status(), battery=68)
    mock_client.get_status.side_effect = None
    mock_client.get_status.return_value = latest
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data[TEST_DEVICE.id] is latest
    assert hass.states.get(entity_id).state == "68"


async def test_unload_cancels_scheduled_status_refresh(hass, mock_client) -> None:
    """A scheduled poll belongs to the config entry and is cancelled on unload."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entered = Event()
    cleaned = Event()

    async def blocked(device_id, *, total_timeout):
        assert (device_id, total_timeout) == (TEST_DEVICE.id, 125.0)
        entered.set()
        try:
            await Event().wait()
        finally:
            cleaned.set()

    mock_client.get_status.reset_mock()
    mock_client.get_status.side_effect = blocked
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        manual = asyncio.create_task(
            entry.runtime_data.coordinator.async_request_refresh()
        )
        await asyncio.sleep(0)
        mock_client.get_status.assert_awaited_once()
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
    await manual
    assert cleaned.is_set()
    mock_client.get_status.assert_awaited_once()


async def test_runtime_status_auth_failure_starts_reauth(hass, mock_client) -> None:
    """A status authentication failure triggers HA reauth through the coordinator."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    mock_client.get_status.reset_mock()
    mock_client.get_status.side_effect = AuthenticationError("expired")

    with patch.object(entry, "async_start_reauth") as start_reauth:
        await coordinator.async_refresh()

    start_reauth.assert_called_once_with(hass, None, None)
    assert not coordinator.last_update_success
    mock_client.get_status.assert_awaited_once_with(TEST_DEVICE.id, total_timeout=125.0)


def test_legacy_entry_uses_q_t_client_constructor(hass) -> None:
    """Test existing Q/T entries keep using the legacy public API."""

    http_client = MagicMock()
    with (
        patch("custom_components.botslab360.Botslab360Client") as client_class,
        patch(
            "custom_components.botslab360.get_async_client",
            return_value=http_client,
        ) as get_client,
    ):
        client = create_client_from_entry_data(hass, TEST_CREDENTIALS)

    assert client is client_class.return_value
    client_class.assert_called_once_with(
        TEST_CREDENTIALS["q"],
        TEST_CREDENTIALS["t"],
        http_client=http_client,
    )
    get_client.assert_called_once_with(hass)
    client_class.from_credentials.assert_not_called()


def test_native_entry_uses_saved_backend_and_identity(hass) -> None:
    """Test native entries reconstruct their credential client exactly."""

    http_client = MagicMock()
    with (
        patch("custom_components.botslab360.Botslab360Client") as client_class,
        patch(
            "custom_components.botslab360.get_async_client",
            return_value=http_client,
        ) as get_client,
    ):
        client = create_client_from_entry_data(hass, TEST_NATIVE_ENTRY_DATA)

    assert client is client_class.from_credentials.return_value
    client_class.assert_not_called()
    call = client_class.from_credentials.call_args
    assert call.kwargs["email"] == TEST_NATIVE_ENTRY_DATA["email"]
    assert call.kwargs["password"] == TEST_NATIVE_ENTRY_DATA["password"]
    assert call.kwargs["backend"] is AuthBackend.ROBOT360
    assert call.kwargs["http_client"] is http_client
    assert call.kwargs["device_identity"].mid == ("0123456789abcdef0123456789abcdef")
    assert call.kwargs["device_identity"].android_id == ("0123456789abcdef")
    assert call.kwargs["device_identity"].m2 == ("00000000-0000-4000-8000-000000000001")
    assert "region" not in call.kwargs
    get_client.assert_called_once_with(hass)


def test_native_entry_prefers_cached_q_t_client(hass) -> None:
    """Test cached native credentials remain distinct from legacy entry data."""

    data = {
        **TEST_NATIVE_ENTRY_DATA,
        CONF_CACHED_Q: TEST_CACHED_CREDENTIALS.q,
        CONF_CACHED_T: TEST_CACHED_CREDENTIALS.t,
    }
    http_client = MagicMock()
    with (
        patch("custom_components.botslab360.Botslab360Client") as client_class,
        patch(
            "custom_components.botslab360.get_async_client",
            return_value=http_client,
        ) as get_client,
    ):
        client = create_client_from_entry_data(hass, data)

    assert client is client_class.return_value
    client_class.assert_called_once_with(
        TEST_CACHED_CREDENTIALS.q,
        TEST_CACHED_CREDENTIALS.t,
        http_client=http_client,
    )
    get_client.assert_called_once_with(hass)
    client_class.from_credentials.assert_not_called()


async def test_coordinator_translates_api_error(hass, mock_client) -> None:
    """Test an API error becomes an UpdateFailed error."""

    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    mock_client.get_status.side_effect = ApiError("unavailable")

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "cannot_connect"
    assert raised.value.__cause__ is mock_client.get_status.side_effect


@pytest.mark.parametrize("stage", ["setup", "update"])
@pytest.mark.parametrize("with_codes", [False, True])
async def test_coordinator_logs_safe_api_diagnostics(
    hass, mock_client, caplog, stage, with_codes
) -> None:
    """Both API failure paths retain translation and log safe optional metadata."""

    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    message = (
        "Device discovery request failed"
        if stage == "setup"
        else "Timed out waiting for robot status"
    )
    error = ApiError(
        message,
        phase="transport" if stage == "setup" else "timeout",
        status_code=503 if with_codes else None,
        errno=7 if with_codes else None,
        response_errno=8 if with_codes else None,
        error_code=9 if with_codes else None,
    )
    timeout = TimeoutError("synthetic private transport context")
    timeout.__cause__ = CancelledError("synthetic private cancellation context")
    error.__cause__ = timeout
    call = mock_client.get_devices if stage == "setup" else mock_client.get_status
    call.side_effect = error
    caplog.set_level(logging.DEBUG, logger=DIAGNOSTIC_LOGGER)

    with pytest.raises(UpdateFailed) as raised:
        if stage == "setup":
            await coordinator._async_setup()
        else:
            await coordinator._async_update_data()

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "cannot_connect"
    assert raised.value.__cause__ is error
    call.assert_awaited_once()
    records = [record for record in caplog.records if record.name == DIAGNOSTIC_LOGGER]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.DEBUG
    assert f"Botslab 360 {stage} failed:" in record.getMessage()
    assert f"phase={error.phase}" in record.getMessage()
    for key in ("status_code", "errno", "response_errno", "error_code"):
        assert f"{key}={getattr(error, key)}" in record.getMessage()
    assert f"message={message}" in record.getMessage()
    assert (
        "exception_chain=ApiError -> TimeoutError -> CancelledError"
        in record.getMessage()
    )
    assert "synthetic private transport context" not in caplog.text
    assert "synthetic private cancellation context" not in caplog.text
    assert record.exc_info is None


@pytest.mark.parametrize("stage", ["setup", "update"])
async def test_coordinator_diagnostics_do_not_log_secrets(
    hass, mock_client, caplog, stage
) -> None:
    """Arbitrary text, cause text, request details and malformed codes stay private."""

    secrets = (
        "synthetic-private-q",
        "synthetic-private-t",
        "synthetic-private-qid",
        "synthetic-private-sid",
        "synthetic-private-push-key",
        "synthetic-private-password",
        "private-user@example.invalid",
        "synthetic-private-cookie",
        "synthetic-private-authorization",
    )
    private_text = " ".join(secrets)
    error = ApiError(private_text, phase=private_text)
    for key in ("status_code", "errno", "response_errno", "error_code"):
        setattr(error, key, private_text)
    error.__cause__ = ReadError(private_text)
    # Cyclic chains must neither hang diagnostics nor expose their text.
    error.__cause__.__context__ = error
    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    call = mock_client.get_devices if stage == "setup" else mock_client.get_status
    call.side_effect = error
    caplog.set_level(logging.DEBUG, logger=DIAGNOSTIC_LOGGER)

    with pytest.raises(UpdateFailed):
        if stage == "setup":
            await coordinator._async_setup()
        else:
            await coordinator._async_update_data()

    assert "message=[redacted]" in caplog.text
    assert "phase=unknown" in caplog.text
    assert "exception_chain=ApiError -> ReadError" in caplog.text
    assert (
        "status_code=None errno=None response_errno=None error_code=None" in caplog.text
    )
    for secret in (*secrets, TEST_DEVICE.id, *TEST_CREDENTIALS.values()):
        assert secret not in caplog.text


@pytest.mark.parametrize("stage", ["setup", "update"])
async def test_coordinator_debug_does_not_enable_safe_diagnostics(
    hass, mock_client, caplog, stage
) -> None:
    """Debugging the coordinator alone does not enable the separate diagnostics."""

    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    call = mock_client.get_devices if stage == "setup" else mock_client.get_status
    call.side_effect = ApiError("Push connection failed", phase="transport")
    caplog.set_level(logging.DEBUG, logger=COORDINATOR_LOGGER)
    caplog.set_level(logging.WARNING, logger=DIAGNOSTIC_LOGGER)

    with pytest.raises(UpdateFailed):
        if stage == "setup":
            await coordinator._async_setup()
        else:
            await coordinator._async_update_data()

    assert "Botslab 360" not in caplog.text


async def test_diagnostics_debug_keeps_home_assistant_traceback_disabled(
    hass, mock_client, caplog
) -> None:
    """Safe diagnostics alone preserve the normal HA error without its traceback."""

    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    error = ApiError("Timed out waiting for push registration", phase="timeout")
    error.__cause__ = TimeoutError("synthetic private transport context")
    mock_client.get_status.side_effect = error
    caplog.set_level(logging.WARNING, logger=COORDINATOR_LOGGER)
    caplog.set_level(logging.DEBUG, logger=DIAGNOSTIC_LOGGER)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert any(
        record.name == COORDINATOR_LOGGER
        and "Error fetching botslab360 data" in record.getMessage()
        for record in caplog.records
    )
    assert any(
        record.name == DIAGNOSTIC_LOGGER
        and "message=Timed out waiting for push registration" in record.getMessage()
        for record in caplog.records
    )
    assert "Full error:" not in caplog.text
    assert "synthetic private transport context" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("stage", ["setup", "update"])
async def test_coordinator_translates_authentication_error(
    hass, mock_client, caplog, stage
) -> None:
    """Test an authentication error triggers Home Assistant reauth."""

    coordinator = Botslab360Coordinator(hass, _entry(), mock_client)
    coordinator.devices = {TEST_DEVICE.id: TEST_DEVICE}
    call = mock_client.get_devices if stage == "setup" else mock_client.get_status
    call.side_effect = AuthenticationError("expired")
    caplog.set_level(logging.DEBUG, logger=DIAGNOSTIC_LOGGER)

    with pytest.raises(ConfigEntryAuthFailed) as raised:
        if stage == "setup":
            await coordinator._async_setup()
        else:
            await coordinator._async_update_data()

    assert raised.value.translation_key == "invalid_auth"
    assert raised.value.__cause__ is call.side_effect
    assert "Botslab 360 setup failed" not in caplog.text
    assert "Botslab 360 update failed" not in caplog.text


async def test_entry_debug_reports_installed_library_version(
    hass, mock_client, caplog
) -> None:
    """Report the installed distribution rather than the manifest pin."""
    entry = _entry()
    entry.add_to_hass(hass)
    caplog.set_level(logging.DEBUG, logger=DIAGNOSTIC_LOGGER)
    with patch(
        "custom_components.botslab360.distribution_version", return_value="0.7.6b99"
    ) as read_version:
        assert await hass.config_entries.async_setup(entry.entry_id)

    read_version.assert_called_once_with("botslab360")
    assert "Loaded botslab360 library version 0.7.6b99" in caplog.text


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
    factory.assert_called_once_with(hass, TEST_NATIVE_ENTRY_DATA)
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

    http_client = MagicMock()
    with (
        patch("custom_components.botslab360.Botslab360Client") as client_class,
        patch(
            "custom_components.botslab360.get_async_client",
            return_value=http_client,
        ),
    ):
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
        http_client=http_client,
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
