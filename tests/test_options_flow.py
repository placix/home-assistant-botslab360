"""Tests for Options Flow client ownership and unloaded-entry authentication."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botslab360 import ApiError, AuthenticationError, CaptchaChallenge, CaptchaRequired
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import (
    CONF_AUTH_BACKEND,
    CONF_CACHED_Q,
    CONF_CACHED_T,
    DOMAIN,
)

from .conftest import (
    TEST_ACCOUNT_FINGERPRINT,
    TEST_CACHED_CREDENTIALS,
    TEST_CREDENTIALS,
    TEST_DEVICE,
    TEST_NATIVE_ENTRY_DATA,
    make_mock_client,
)

CACHED_ENTRY_DATA = {
    **TEST_NATIVE_ENTRY_DATA,
    CONF_CACHED_Q: "synthetic-old-q",
    CONF_CACHED_T: "synthetic-old-t",
}


def _entry(hass, data=TEST_CREDENTIALS):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=TEST_ACCOUNT_FINGERPRINT, data=data
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize("auth_error", [False, True])
async def test_options_reuses_loaded_client(hass, mock_client, auth_error) -> None:
    """Runtime clients are neither reauthenticated nor closed by Options."""

    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.reset_mock()
    if auth_error:
        mock_client.get_rooms.side_effect = AuthenticationError("expired")

    with patch("custom_components.botslab360.create_client_from_entry_data") as factory:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.async_block_till_done()

    factory.assert_not_called()
    mock_client.authenticate.assert_not_awaited()
    mock_client.close.assert_not_awaited()
    mock_client.get_rooms.assert_awaited_once_with(TEST_DEVICE)
    assert result["errors"] == ({"base": "invalid_auth"} if auth_error else {})
    assert (
        bool(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == auth_error
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "data",
    [TEST_CREDENTIALS, TEST_NATIVE_ENTRY_DATA, CACHED_ENTRY_DATA],
    ids=["legacy", "native", "cached"],
)
async def test_options_without_runtime_uses_temporary_client(hass, data) -> None:
    """All stored auth formats use HA HTTPX and release only the temporary wrapper."""

    entry = _entry(hass, data)
    assert not hasattr(entry, "runtime_data")
    client = make_mock_client()
    http_client = MagicMock()
    http_client.aclose = AsyncMock()
    with (
        patch("custom_components.botslab360.Botslab360Client") as client_class,
        patch(
            "custom_components.botslab360.get_async_client", return_value=http_client
        ) as get_client,
    ):
        client_class.return_value = client
        client_class.from_credentials.return_value = client
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    assert {field.schema for field in result["data_schema"].schema} >= {"Bad", "Closet"}
    constructor = (
        client_class.from_credentials
        if data is TEST_NATIVE_ENTRY_DATA
        else client_class
    )
    assert constructor.call_count == 1
    assert constructor.call_args.kwargs["http_client"] is http_client
    if data is not TEST_NATIVE_ENTRY_DATA:
        client_class.from_credentials.assert_not_called()
    get_client.assert_called_once_with(hass)
    client.authenticate.assert_awaited_once()
    client.get_devices.assert_awaited_once()
    client.get_rooms.assert_awaited_once_with(TEST_DEVICE)
    calls = [call[0] for call in client.mock_calls]
    assert calls.index("authenticate") < calls.index("get_devices")
    client.close.assert_awaited_once()
    http_client.aclose.assert_not_awaited()
    assert not hasattr(entry, "runtime_data")
    if data is not TEST_CREDENTIALS:
        assert entry.data[CONF_CACHED_Q] == TEST_CACHED_CREDENTIALS.q
        assert entry.data[CONF_CACHED_T] == TEST_CACHED_CREDENTIALS.t

    # Redisplaying the form reuses discovered rooms, not a closed temporary client.
    await hass.config_entries.options.async_configure(result["flow_id"])
    client.get_devices.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.parametrize("runtime_data", [None, SimpleNamespace(client=None)])
async def test_options_without_runtime_client(hass, mock_client, runtime_data) -> None:
    """An empty runtime container is handled like absent runtime_data."""

    entry = _entry(hass)
    entry.runtime_data = runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["errors"] == {}
    mock_client.authenticate.assert_awaited_once()
    mock_client.close.assert_awaited_once()


@pytest.mark.parametrize("method", ["authenticate", "get_devices", "get_rooms"])
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("expired"), "invalid_auth"),
        (ApiError("unavailable"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
        (OSError(), "cannot_connect"),
    ],
)
async def test_temporary_client_failure(
    hass, mock_client, method, error, expected
) -> None:
    """Authentication/discovery failures close the owned client and show safe errors."""

    entry = _entry(hass)
    getattr(mock_client, method).side_effect = error
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.async_block_till_done()

    assert result["errors"] == {"base": expected}
    mock_client.close.assert_awaited_once()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    if expected == "invalid_auth":
        assert len(flows) == 1
        assert flows[0]["context"]["source"] == SOURCE_REAUTH
        assert flows[0]["context"]["entry_id"] == entry.entry_id
    else:
        assert not flows


@pytest.mark.parametrize("failure_at", ["creation", "authenticate", "get_rooms"])
async def test_options_cached_credentials_fallback(hass, failure_at) -> None:
    """Invalid cached credentials fall back once and refresh the saved Q/T."""

    entry = _entry(hass, CACHED_ENTRY_DATA)
    cached_client = make_mock_client()
    native_client = make_mock_client()
    if failure_at != "creation":
        getattr(cached_client, failure_at).side_effect = AuthenticationError("expired")
    with (
        patch(
            "custom_components.botslab360.create_client_from_entry_data",
            return_value=cached_client,
            side_effect=ValueError("invalid cache")
            if failure_at == "creation"
            else None,
        ) as cached_factory,
        patch(
            "custom_components.botslab360.create_native_client_from_entry_data",
            return_value=native_client,
        ) as native_factory,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["errors"] == {}
    cached_factory.assert_called_once_with(hass, CACHED_ENTRY_DATA)
    native_factory.assert_called_once_with(hass, CACHED_ENTRY_DATA)
    assert cached_client.close.await_count == (0 if failure_at == "creation" else 1)
    native_client.authenticate.assert_awaited_once()
    native_client.close.assert_awaited_once()
    assert entry.data[CONF_CACHED_Q] == TEST_CACHED_CREDENTIALS.q
    assert entry.data[CONF_CACHED_T] == TEST_CACHED_CREDENTIALS.t


async def test_options_fallback_captcha_starts_reauth(hass) -> None:
    """Interactive native fallback is not retried or exposed in the Options flow."""

    entry = _entry(hass, CACHED_ENTRY_DATA)
    cached_client = make_mock_client()
    cached_client.authenticate.side_effect = AuthenticationError("expired")
    native_client = make_mock_client()
    native_client.authenticate.side_effect = CaptchaRequired(
        CaptchaChallenge(image=b"synthetic-image", sc="synthetic-context")
    )
    with (
        patch(
            "custom_components.botslab360.create_client_from_entry_data",
            return_value=cached_client,
        ),
        patch(
            "custom_components.botslab360.create_native_client_from_entry_data",
            return_value=native_client,
        ) as native_factory,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.async_block_till_done()

    assert result["errors"] == {"base": "invalid_auth"}
    native_factory.assert_called_once()
    cached_client.close.assert_awaited_once()
    native_client.close.assert_awaited_once()
    native_client.authenticate.assert_awaited_once()
    assert entry.data == CACHED_ENTRY_DATA
    assert any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )


async def test_options_invalid_entry_data_starts_reauth(hass) -> None:
    """Invalid saved backend data produces invalid_auth, not an uncaught ValueError."""

    entry = _entry(
        hass, {**TEST_NATIVE_ENTRY_DATA, CONF_AUTH_BACKEND: "invalid-backend"}
    )
    with patch("botslab360.Botslab360Client.authenticate") as authenticate:
        result = await hass.config_entries.options.async_init(entry.entry_id)
    authenticate.assert_not_awaited()
    await hass.async_block_till_done()
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_options_no_devices_closes_temporary_client(hass, mock_client) -> None:
    """An empty account retains the existing no_devices error and releases the client."""

    entry = _entry(hass)
    mock_client.get_devices.return_value = []
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["errors"] == {"base": "no_devices"}
    mock_client.close.assert_awaited_once()
