"""Tests for the Botslab 360 config flow."""

from unittest.mock import patch

import pytest

from botslab360 import (
    ApiError,
    AuthenticationError,
    CaptchaChallenge,
    CaptchaRequired,
)
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelectorType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360 import take_authenticated_client
from custom_components.botslab360.config_flow import CONF_CAPTCHA_CODE
from custom_components.botslab360.const import (
    CONF_AUTH_BACKEND,
    CONF_DEVICE_IDENTITY,
    DATA_AUTHENTICATED_CLIENTS,
    DOMAIN,
)

from .conftest import (
    TEST_ACCOUNT_FINGERPRINT,
    TEST_CREDENTIALS,
    TEST_DEVICE_IDENTITY,
    TEST_NATIVE_ENTRY_DATA,
    TEST_NATIVE_INPUT,
    make_mock_client,
)

TEST_CAPTCHA_CODE = "not-a-real-captcha-code"
TEST_CAPTCHA = CaptchaChallenge(
    image=b"\x89PNG\r\n\x1a\nnot-a-real-image",
    sc="not-a-real-secret-context",
)


async def _start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )


async def _submit_user_flow(hass, user_input=TEST_NATIVE_INPUT):
    initial = await _start_user_flow(hass)
    return await hass.config_entries.flow.async_configure(
        initial["flow_id"], user_input
    )


@pytest.mark.parametrize("backend", ["robot360", "botslab"])
async def test_successful_config_flow(hass, mock_client, backend) -> None:
    """Test authenticated clients cross the real flow-to-setup boundary."""

    initial = await _start_user_flow(hass)
    assert initial["type"] is FlowResultType.FORM
    assert initial["step_id"] == "user"
    selectors = {
        marker.schema: selector
        for marker, selector in initial["data_schema"].schema.items()
    }
    assert set(selectors) == {CONF_AUTH_BACKEND, CONF_EMAIL, CONF_PASSWORD}
    assert selectors[CONF_EMAIL].config["type"] == TextSelectorType.EMAIL
    assert selectors[CONF_PASSWORD].config["type"] == TextSelectorType.PASSWORD

    user_input = {**TEST_NATIVE_INPUT, CONF_AUTH_BACKEND: backend}
    expected_data = {**TEST_NATIVE_ENTRY_DATA, CONF_AUTH_BACKEND: backend}
    with patch(
        "custom_components.botslab360.create_client_from_entry_data",
        side_effect=AssertionError("setup must consume the authenticated client"),
    ) as setup_factory:
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], user_input
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Robot"
    assert result["data"] == expected_data
    assert result["result"].unique_id == TEST_ACCOUNT_FINGERPRINT
    assert "region" not in result["data"]
    assert "qid" not in result["data"]
    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is mock_client
    mock_client.authenticate.assert_awaited_once()
    setup_factory.assert_not_called()
    assert mock_client.get_devices.await_count == 2
    mock_client.close.assert_not_awaited()
    assert hass.states.get("vacuum.test_robot") is not None
    assert hass.states.get("sensor.test_robot_battery") is not None
    assert hass.states.get("camera.test_robot_map") is not None
    assert hass.data[DOMAIN][DATA_AUTHENTICATED_CLIENTS] == {}

    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (AuthenticationError("invalid"), "invalid_auth"),
        (ApiError("unavailable"), "cannot_connect"),
    ],
)
async def test_native_authentication_errors(
    hass, mock_client, exception, error
) -> None:
    """Test native authentication failures do not create entries."""

    mock_client.authenticate.side_effect = exception
    result = await _submit_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)
    mock_client.close.assert_awaited_once()


async def test_no_devices(hass, mock_client) -> None:
    """Test accounts without devices are rejected."""

    mock_client.get_devices.return_value = []
    result = await _submit_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}
    assert not hass.config_entries.async_entries(DOMAIN)
    mock_client.close.assert_awaited_once()


async def test_duplicate_account_is_prevented(hass, mock_client) -> None:
    """Test a stable account fingerprint prevents duplicate entries."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)

    result = await _submit_user_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    mock_client.close.assert_awaited_once()
    assert not hass.data.get(DOMAIN, {}).get(DATA_AUTHENTICATED_CLIENTS)


async def test_captcha_continues_on_same_client(hass, mock_client) -> None:
    """Test captcha continuation retains its client, challenge, and identity."""

    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    with (
        patch(
            "custom_components.botslab360.config_flow.create_client_from_entry_data",
            return_value=mock_client,
        ) as factory_mock,
        patch(
            "custom_components.botslab360.config_flow.DeviceIdentity.generate",
            return_value=TEST_DEVICE_IDENTITY,
        ) as identity_mock,
    ):
        captcha = await _submit_user_flow(hass)

        assert captcha["type"] is FlowResultType.FORM
        assert captcha["step_id"] == "captcha"
        image_url = captcha["description_placeholders"]["captcha_image"]
        assert image_url.startswith("data:image/png;base64,")
        assert TEST_CAPTCHA.sc not in image_url
        mock_client.close.assert_not_awaited()

        with patch(
            "custom_components.botslab360.create_client_from_entry_data",
            side_effect=AssertionError(
                "setup must consume the captcha-authenticated client"
            ),
        ) as setup_factory:
            result = await hass.config_entries.flow.async_configure(
                captcha["flow_id"],
                {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE},
            )
            await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == TEST_NATIVE_ENTRY_DATA
    assert CONF_CAPTCHA_CODE not in result["data"]
    assert "sc" not in result["data"]
    factory_mock.assert_called_once_with(TEST_NATIVE_ENTRY_DATA)
    identity_mock.assert_called_once_with()
    mock_client.continue_authentication.assert_awaited_once()
    args = mock_client.continue_authentication.await_args.args
    assert args[0] is TEST_CAPTCHA
    assert args[1] == TEST_CAPTCHA_CODE
    assert result["result"].state is ConfigEntryState.LOADED
    assert result["result"].runtime_data.client is mock_client
    mock_client.authenticate.assert_awaited_once()
    setup_factory.assert_not_called()
    mock_client.close.assert_not_awaited()
    assert hass.data[DOMAIN][DATA_AUTHENTICATED_CLIENTS] == {}

    assert await hass.config_entries.async_unload(result["result"].entry_id)
    mock_client.close.assert_awaited_once()


async def test_incorrect_captcha_stays_in_captcha_step(hass, mock_client) -> None:
    """Test an incorrect captcha is reported without leaking its value."""

    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    mock_client.continue_authentication.side_effect = AuthenticationError("5011")
    captcha = await _submit_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        captcha["flow_id"],
        {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "captcha"
    assert result["errors"] == {"base": "invalid_captcha"}
    assert TEST_CAPTCHA_CODE not in str(result)
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_abandoned_captcha_flow_closes_pending_client(
    hass, mock_client
) -> None:
    """Test removing a captcha flow closes its retained client."""

    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    captcha = await _submit_user_flow(hass)

    hass.config_entries.flow.async_abort(captcha["flow_id"])
    await hass.async_block_till_done()

    mock_client.close.assert_awaited_once()
    assert not hass.config_entries.flow.async_progress()


async def test_no_devices_after_captcha_returns_to_user(hass, mock_client) -> None:
    """Test discovery failures after captcha return to the origin form."""

    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    mock_client.get_devices.return_value = []
    captcha = await _submit_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        captcha["flow_id"],
        {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE},
    )

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices"}
    mock_client.close.assert_awaited_once()


async def test_legacy_reauthentication_updates_entry(hass, mock_client) -> None:
    """Test existing Q/T entries retain their legacy reauth flow."""

    old_data = {"q": "expired-test-q", "t": "expired-test-t"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=old_data,
    )
    entry.add_to_hass(hass)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload_mock:
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=old_data,
        )
        assert initial["step_id"] == "reauth_legacy"
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], TEST_CREDENTIALS
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == TEST_CREDENTIALS
    reload_mock.assert_called_once_with(entry.entry_id)
    assert take_authenticated_client(hass, entry.unique_id) is mock_client
    await mock_client.close()


async def test_native_reauthentication_preserves_identity(
    hass, mock_client
) -> None:
    """Test native reauth updates credentials without replacing identity."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    updated = {
        CONF_EMAIL: "updated@example.invalid",
        CONF_PASSWORD: "new-not-real-password",
    }

    with patch.object(hass.config_entries, "async_schedule_reload") as reload_mock:
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert initial["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], updated
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_EMAIL] == updated[CONF_EMAIL]
    assert entry.data[CONF_PASSWORD] == updated[CONF_PASSWORD]
    assert entry.data[CONF_DEVICE_IDENTITY] == TEST_NATIVE_ENTRY_DATA[
        CONF_DEVICE_IDENTITY
    ]
    assert entry.data[CONF_AUTH_BACKEND] == "robot360"
    reload_mock.assert_called_once_with(entry.entry_id)
    assert take_authenticated_client(hass, entry.unique_id) is mock_client
    await mock_client.close()


async def test_native_reauthentication_reuses_client_during_real_reload(
    hass, mock_client
) -> None:
    """Test a successful reauth reload does not authenticate twice."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.botslab360.create_client_from_entry_data",
        return_value=mock_client,
    ) as setup_factory:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert setup_factory.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    mock_client.authenticate.assert_awaited_once()

    reauth_client = make_mock_client()
    updated = {
        CONF_EMAIL: "updated@example.invalid",
        CONF_PASSWORD: "new-not-real-password",
    }
    with (
        patch(
            "custom_components.botslab360.config_flow.create_client_from_entry_data",
            return_value=reauth_client,
        ),
        patch(
            "custom_components.botslab360.create_client_from_entry_data",
            side_effect=AssertionError(
                "reload must consume the reauthenticated client"
            ),
        ) as reload_factory,
    ):
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], updated
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is reauth_client
    reload_factory.assert_not_called()
    reauth_client.authenticate.assert_awaited_once()
    assert reauth_client.get_devices.await_count == 2
    reauth_client.close.assert_not_awaited()
    mock_client.close.assert_awaited_once()
    assert hass.data[DOMAIN][DATA_AUTHENTICATED_CLIENTS] == {}

    assert await hass.config_entries.async_unload(entry.entry_id)
    reauth_client.close.assert_awaited_once()


async def test_native_reauth_rejects_different_account(hass, mock_client) -> None:
    """Test native reauth retains the existing account identity."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    mock_client.account_fingerprint = "different-account-fingerprint"

    initial = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"],
        {
            CONF_EMAIL: TEST_NATIVE_INPUT[CONF_EMAIL],
            CONF_PASSWORD: TEST_NATIVE_INPUT[CONF_PASSWORD],
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "reauth_wrong_account"}
    assert entry.data == TEST_NATIVE_ENTRY_DATA
    mock_client.close.assert_awaited_once()
    assert not hass.data.get(DOMAIN, {}).get(DATA_AUTHENTICATED_CLIENTS)


async def test_native_reauth_supports_captcha(hass, mock_client) -> None:
    """Test native reauth can continue an authentication captcha."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_NATIVE_ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    updated = {
        CONF_EMAIL: "updated@example.invalid",
        CONF_PASSWORD: "new-not-real-password",
    }

    initial = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    captcha = await hass.config_entries.flow.async_configure(
        initial["flow_id"], updated
    )
    assert captcha["step_id"] == "captcha"

    with patch.object(hass.config_entries, "async_schedule_reload") as reload_mock:
        result = await hass.config_entries.flow.async_configure(
            captcha["flow_id"],
            {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_DEVICE_IDENTITY] == TEST_NATIVE_ENTRY_DATA[
        CONF_DEVICE_IDENTITY
    ]
    mock_client.continue_authentication.assert_awaited_once_with(
        TEST_CAPTCHA,
        TEST_CAPTCHA_CODE,
    )
    reload_mock.assert_called_once_with(entry.entry_id)
    assert take_authenticated_client(hass, entry.unique_id) is mock_client
    await mock_client.close()
