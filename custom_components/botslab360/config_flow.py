"""Config flow for Botslab 360."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botslab360 import ApiError, AuthenticationError, Botslab360Client
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_Q, CONF_T, DOMAIN


class NoDevicesError(Exception):
    """Raised when an account contains no supported devices."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validated account details needed by the config flow."""

    account_fingerprint: str
    title: str


def _credentials_schema() -> vol.Schema:
    """Return the Q/T credentials schema."""

    password_selector = TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    )
    return vol.Schema(
        {
            vol.Required(CONF_Q): password_selector,
            vol.Required(CONF_T): password_selector,
        }
    )


async def _async_validate_input(user_input: dict[str, Any]) -> ValidationResult:
    """Authenticate and discover devices without retaining a client."""

    client: Botslab360Client | None = None
    try:
        client = Botslab360Client(user_input[CONF_Q], user_input[CONF_T])
        await client.authenticate()
        devices = await client.get_devices()
        if not devices:
            raise NoDevicesError

        return ValidationResult(
            account_fingerprint=client.account_fingerprint,
            title=devices[0].name or "Botslab 360",
        )
    finally:
        if client is not None:
            await client.close()


class Botslab360ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Botslab 360."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial configuration step."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                result = await _async_validate_input(user_input)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except NoDevicesError:
                errors["base"] = "no_devices"
            except (ApiError, TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(result.account_fingerprint)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=result.title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(),
            errors=errors,
        )

    async def async_step_reauth(
        self, _entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials and reload the existing entry."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                result = await _async_validate_input(user_input)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except NoDevicesError:
                errors["base"] = "no_devices"
            except (ApiError, TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            else:
                reauth_entry = self._get_reauth_entry()
                if result.account_fingerprint != reauth_entry.unique_id:
                    errors["base"] = "reauth_wrong_account"
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data_updates=user_input,
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(),
            errors=errors,
        )
