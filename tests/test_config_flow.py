"""Tests for the Botslab 360 config flow."""

from unittest.mock import AsyncMock, patch

from botslab360 import ApiError, AuthenticationError
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelectorType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.const import DOMAIN

from .conftest import (
    TEST_ACCOUNT_FINGERPRINT,
    TEST_CREDENTIALS,
)


async def _start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )


async def test_successful_config_flow(hass, mock_client) -> None:
    """Test successful account setup and device discovery."""

    initial = await _start_user_flow(hass)
    assert initial["type"] is FlowResultType.FORM
    assert initial["step_id"] == "user"
    selectors = {
        marker.schema: selector
        for marker, selector in initial["data_schema"].schema.items()
    }
    assert selectors["q"].config["type"] == TextSelectorType.PASSWORD
    assert selectors["t"].config["type"] == TextSelectorType.PASSWORD

    with patch(
        "custom_components.botslab360.async_setup_entry",
        new=AsyncMock(return_value=True),
    ) as setup_entry_mock:
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], TEST_CREDENTIALS
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Robot"
    assert result["data"] == TEST_CREDENTIALS
    assert result["result"].unique_id == TEST_ACCOUNT_FINGERPRINT
    assert "qid" not in result["data"]
    mock_client.authenticate.assert_awaited_once()
    mock_client.get_devices.assert_awaited_once()
    mock_client.close.assert_awaited_once()
    setup_entry_mock.assert_awaited_once()


async def test_invalid_auth(hass, mock_client) -> None:
    """Test invalid credentials are reported without creating an entry."""

    mock_client.authenticate.side_effect = AuthenticationError("invalid")
    initial = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], TEST_CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_cannot_connect(hass, mock_client) -> None:
    """Test API communication failures are reported separately."""

    mock_client.authenticate.side_effect = ApiError("unavailable")
    initial = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], TEST_CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_no_devices(hass, mock_client) -> None:
    """Test accounts without devices are rejected."""

    mock_client.get_devices.return_value = []
    initial = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], TEST_CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_duplicate_account_is_prevented(hass, mock_client) -> None:
    """Test a stable account fingerprint prevents duplicate entries."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_ACCOUNT_FINGERPRINT,
        data=TEST_CREDENTIALS,
    )
    entry.add_to_hass(hass)

    initial = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], TEST_CREDENTIALS
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauthentication_updates_and_reloads_entry(
    hass, mock_client
) -> None:
    """Test reauthentication updates the existing entry only."""

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
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], TEST_CREDENTIALS
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == TEST_CREDENTIALS
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    reload_mock.assert_called_once_with(entry.entry_id)
