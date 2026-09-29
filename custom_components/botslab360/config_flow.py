"""Config flow for Botslab 360."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.selector import (
    AreaSelector,
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from botslab360 import (
    ApiError,
    AuthBackend,
    AuthenticationError,
    Botslab360Client,
    CaptchaChallenge,
    CaptchaRequired,
    DeviceIdentity,
)

from . import (
    async_discard_authenticated_client,
    async_store_authenticated_client,
    create_client_from_entry_data,
    create_native_client_from_entry_data,
)
from .areas import DiscoveredRoom, RoomAreaField, room_area_fields
from .const import (
    CONF_AUTH_BACKEND,
    CONF_DEVICE_IDENTITY,
    CONF_IDENTITY_ANDROID_ID,
    CONF_IDENTITY_M2,
    CONF_IDENTITY_MID,
    CONF_IGNORED_ROOMS,
    CONF_Q,
    CONF_ROOM_AREAS,
    CONF_ROOM_PREFERENCES,
    CONF_T,
    DOMAIN,
)

CONF_CAPTCHA_CODE = "captcha_code"


class NoDevicesError(Exception):
    """Raised when an account contains no supported devices."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validated account details needed by the config flow."""

    account_fingerprint: str
    title: str
    rooms: tuple[DiscoveredRoom, ...]


def _password_selector() -> TextSelector:
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _email_selector() -> TextSelector:
    return TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))


def _native_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the native credential schema."""

    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_AUTH_BACKEND,
                default=defaults.get(
                    CONF_AUTH_BACKEND,
                    AuthBackend.ROBOT360.value,
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        AuthBackend.ROBOT360.value,
                        AuthBackend.BOTSLAB.value,
                    ],
                    translation_key="auth_backend",
                )
            ),
            vol.Required(
                CONF_EMAIL,
                default=defaults.get(CONF_EMAIL, ""),
            ): _email_selector(),
            vol.Required(CONF_PASSWORD): _password_selector(),
        }
    )


def _native_reauth_schema(email: str) -> vol.Schema:
    """Return the credential reauthentication schema."""

    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=email): _email_selector(),
            vol.Required(CONF_PASSWORD): _password_selector(),
        }
    )


def _legacy_schema() -> vol.Schema:
    """Return the legacy Q/T reauthentication schema."""

    return vol.Schema(
        {
            vol.Required(CONF_Q): _password_selector(),
            vol.Required(CONF_T): _password_selector(),
        }
    )


def _captcha_schema() -> vol.Schema:
    """Return the captcha continuation schema."""

    return vol.Schema({vol.Required(CONF_CAPTCHA_CODE): _password_selector()})


def _identity_data(identity: DeviceIdentity) -> dict[str, str]:
    return {
        CONF_IDENTITY_MID: identity.mid,
        CONF_IDENTITY_ANDROID_ID: identity.android_id,
        CONF_IDENTITY_M2: identity.m2,
    }


def _captcha_data_url(challenge: CaptchaChallenge) -> str:
    """Return an in-memory image URL suitable for a flow description."""

    image = challenge.image
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif image.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    elif image.startswith((b"GIF87a", b"GIF89a")):
        media_type = "image/gif"
    elif image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        media_type = "application/octet-stream"
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


async def _async_discover(
    client: Botslab360Client,
    *,
    include_rooms: bool,
) -> ValidationResult:
    devices = await client.get_devices()
    if not devices:
        raise NoDevicesError
    rooms: list[DiscoveredRoom] = []
    if include_rooms:
        for device in devices:
            rooms.extend(
                DiscoveredRoom(device, room) for room in await client.get_rooms(device)
            )
    return ValidationResult(
        account_fingerprint=client.account_fingerprint,
        title=devices[0].name or "Botslab 360",
        rooms=tuple(rooms),
    )


def _room_area_schema(fields: list[RoomAreaField]) -> vol.Schema:
    """Return Area and ignore selectors for discovered rooms."""

    schema: dict[vol.Marker, AreaSelector | BooleanSelector] = {}
    for field in fields:
        description = (
            {"suggested_value": field.area_id} if field.area_id is not None else None
        )
        marker = vol.Optional(field.label, description=description)
        schema[marker] = AreaSelector()
        schema[
            vol.Optional(
                field.ignore_label,
                description={"suggested_value": field.ignored},
            )
        ] = BooleanSelector()
    return vol.Schema(schema)


def _room_area_mappings(
    fields: list[RoomAreaField],
    user_input: dict[str, Any],
) -> dict[str, str | None]:
    """Convert transient human-readable form fields to stable options keys."""

    return {
        field.discovered_room.mapping_key: user_input.get(field.label)
        for field in fields
    }


def _ignored_room_keys(
    fields: list[RoomAreaField],
    user_input: dict[str, Any],
    existing: set[str] | None = None,
) -> list[str]:
    """Convert room ignore toggles to stable robot/room option keys."""

    ignored = set() if existing is None else set(existing)
    for field in fields:
        mapping_key = field.discovered_room.mapping_key
        if user_input.get(field.ignore_label, field.ignored):
            ignored.add(mapping_key)
        else:
            ignored.discard(mapping_key)
    return sorted(ignored)


class Botslab360ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Botslab 360."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize transient authentication state."""

        self._device_identity: DeviceIdentity | None = None
        self._pending_client: Botslab360Client | None = None
        self._pending_challenge: CaptchaChallenge | None = None
        self._pending_data: dict[str, Any] | None = None
        self._pending_validation: ValidationResult | None = None
        self._pending_reauth = False
        self._last_errors: dict[str, str] = {}

    @callback
    def async_remove(self) -> None:
        """Close a client retained by an abandoned authentication flow."""

        client = self._pending_client
        self._clear_pending_state()
        if client is not None:
            self.hass.async_create_task(
                client.close(),
                "Close abandoned Botslab 360 authentication client",
            )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle native credential configuration."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if self._device_identity is None:
                self._device_identity = DeviceIdentity.generate()
            entry_data = {
                CONF_AUTH_BACKEND: user_input[CONF_AUTH_BACKEND],
                CONF_EMAIL: user_input[CONF_EMAIL],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_DEVICE_IDENTITY: _identity_data(self._device_identity),
            }
            result = await self._async_begin_authentication(
                entry_data,
                reauth=False,
            )
            if result is not None:
                return result
            if self._pending_client is not None:
                return self._show_captcha_form()
            errors = self._last_errors

        return self.async_show_form(
            step_id="user",
            data_schema=_native_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""

        entry = self._get_reauth_entry()
        if CONF_Q in entry.data and CONF_T in entry.data:
            return await self.async_step_reauth_legacy()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_legacy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reauthenticate an existing legacy Q/T entry."""

        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_begin_authentication(
                user_input,
                reauth=True,
            )
            if result is not None:
                return result
            errors = self._last_errors

        return self.async_show_form(
            step_id="reauth_legacy",
            data_schema=_legacy_schema(),
            errors=errors,
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reauthenticate an existing native credential entry."""

        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            entry_data = {
                **entry.data,
                CONF_EMAIL: user_input[CONF_EMAIL],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            result = await self._async_begin_authentication(
                entry_data,
                reauth=True,
            )
            if result is not None:
                return result
            if self._pending_client is not None:
                return self._show_captcha_form()
            errors = self._last_errors

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_native_reauth_schema(entry.data[CONF_EMAIL]),
            errors=errors,
        )

    async def async_step_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue native authentication with a graphic captcha."""

        client = self._pending_client
        challenge = self._pending_challenge
        if client is None or challenge is None or self._pending_data is None:
            return self.async_abort(reason="invalid_auth")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await client.continue_authentication(
                    challenge,
                    user_input[CONF_CAPTCHA_CODE],
                )
            except CaptchaRequired as err:
                self._pending_challenge = err.challenge
                errors["base"] = "invalid_captcha"
            except AuthenticationError:
                errors["base"] = "invalid_captcha"
            except (ApiError, TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            else:
                try:
                    validation = await _async_discover(
                        client,
                        include_rooms=not self._pending_reauth,
                    )
                except NoDevicesError:
                    return await self._async_return_to_origin("no_devices")
                except AuthenticationError:
                    return await self._async_return_to_origin("invalid_auth")
                except (ApiError, TimeoutError, OSError):
                    return await self._async_return_to_origin("cannot_connect")
                data = self._pending_data
                reauth = self._pending_reauth
                return await self._async_finish_validation(
                    validation,
                    data,
                    client,
                    reauth=reauth,
                )

        return self._show_captcha_form(errors)

    async def async_step_room_areas(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Let the user review room-to-area assignments before setup."""

        validation = self._pending_validation
        client = self._pending_client
        entry_data = self._pending_data
        if validation is None or client is None or entry_data is None:
            return self.async_abort(reason="invalid_auth")

        fields = room_area_fields(
            validation.rooms,
            ar.async_get(self.hass).async_list_areas(),
            {},
            (),
        )
        if user_input is None:
            return self.async_show_form(
                step_id="room_areas",
                data_schema=_room_area_schema(fields),
                last_step=True,
            )

        options = {
            CONF_ROOM_AREAS: _room_area_mappings(fields, user_input),
            CONF_IGNORED_ROOMS: _ignored_room_keys(fields, user_input),
            CONF_ROOM_PREFERENCES: {},
        }
        self._clear_pending_state()
        await async_store_authenticated_client(
            self.hass,
            validation.account_fingerprint,
            client,
        )
        try:
            return self.async_create_entry(
                title=validation.title,
                data=entry_data,
                options=options,
            )
        except BaseException:
            await async_discard_authenticated_client(
                self.hass,
                validation.account_fingerprint,
                client,
            )
            raise

    async def _async_begin_authentication(
        self,
        entry_data: dict[str, Any],
        *,
        reauth: bool,
    ) -> ConfigFlowResult | None:
        self._last_errors = {}
        client: Botslab360Client | None = None
        try:
            client = (
                create_native_client_from_entry_data(entry_data)
                if CONF_EMAIL in entry_data
                else create_client_from_entry_data(entry_data)
            )
            await client.authenticate()
            validation = await _async_discover(client, include_rooms=not reauth)
        except CaptchaRequired as err:
            self._pending_client = client
            self._pending_challenge = err.challenge
            self._pending_data = entry_data
            self._pending_reauth = reauth
            return None
        except AuthenticationError:
            self._last_errors["base"] = "invalid_auth"
        except NoDevicesError:
            self._last_errors["base"] = "no_devices"
        except (ApiError, TimeoutError, OSError):
            self._last_errors["base"] = "cannot_connect"
        except (KeyError, ValueError):
            self._last_errors["base"] = "invalid_auth"
        else:
            return await self._async_finish_validation(
                validation,
                entry_data,
                client,
                reauth=reauth,
            )

        if client is not None:
            await client.close()
        return None

    async def _async_finish_validation(
        self,
        validation: ValidationResult,
        entry_data: dict[str, Any],
        client: Botslab360Client,
        *,
        reauth: bool,
    ) -> ConfigFlowResult:
        if reauth:
            entry = self._get_reauth_entry()
            if validation.account_fingerprint != entry.unique_id:
                await client.close()
                if CONF_Q in entry.data and CONF_T in entry.data:
                    return self._show_legacy_reauth_form(
                        {"base": "reauth_wrong_account"}
                    )
                return self._show_native_reauth_form({"base": "reauth_wrong_account"})
            await async_store_authenticated_client(
                self.hass,
                validation.account_fingerprint,
                client,
            )
            self._clear_pending_state()
            try:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=entry_data,
                )
            except BaseException:
                await async_discard_authenticated_client(
                    self.hass,
                    validation.account_fingerprint,
                    client,
                )
                raise

        try:
            await self.async_set_unique_id(validation.account_fingerprint)
            self._abort_if_unique_id_configured()
        except BaseException:
            await client.close()
            raise
        self._pending_client = client
        self._pending_challenge = None
        self._pending_data = entry_data
        self._pending_validation = validation
        self._pending_reauth = False
        return await self.async_step_room_areas()

    async def _async_return_to_origin(self, error: str) -> ConfigFlowResult:
        reauth = self._pending_reauth
        await self._async_clear_pending()
        if reauth:
            return self._show_native_reauth_form({"base": error})
        return self._show_user_form({"base": error})

    async def _async_clear_pending(self) -> None:
        if self._pending_client is not None:
            await self._pending_client.close()
        self._clear_pending_state()

    def _clear_pending_state(self) -> None:
        """Forget transient captcha state after ownership is transferred."""

        self._pending_client = None
        self._pending_challenge = None
        self._pending_data = None
        self._pending_validation = None
        self._pending_reauth = False

    def _show_captcha_form(
        self,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        assert self._pending_challenge is not None
        return self.async_show_form(
            step_id="captcha",
            data_schema=_captcha_schema(),
            errors=errors or {},
            description_placeholders={
                "captcha_image": _captcha_data_url(self._pending_challenge)
            },
        )

    def _show_user_form(
        self,
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="user",
            data_schema=_native_schema(),
            errors=errors,
        )

    def _show_native_reauth_form(
        self,
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_native_reauth_schema(entry.data[CONF_EMAIL]),
            errors=errors,
        )

    def _show_legacy_reauth_form(
        self,
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="reauth_legacy",
            data_schema=_legacy_schema(),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> Botslab360OptionsFlow:
        """Return the room-area options flow."""

        return Botslab360OptionsFlow()


class Botslab360OptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage mutable room-to-area assignments."""

    def __init__(self) -> None:
        """Initialize room discovery state."""

        self._rooms: tuple[DiscoveredRoom, ...] | None = None

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Discover rooms and edit their Home Assistant Areas."""

        errors: dict[str, str] = {}
        if self._rooms is None:
            try:
                validation = await _async_discover(
                    self.config_entry.runtime_data.client,
                    include_rooms=True,
                )
            except AuthenticationError:
                self.config_entry.async_start_reauth(self.hass)
                errors["base"] = "invalid_auth"
            except NoDevicesError:
                errors["base"] = "no_devices"
            except (ApiError, TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            else:
                self._rooms = validation.rooms

        fields: list[RoomAreaField] = []
        if self._rooms is not None:
            configured_mappings = self.config_entry.options.get(CONF_ROOM_AREAS, {})
            ignored_rooms = set(self.config_entry.options.get(CONF_IGNORED_ROOMS, ()))
            fields = room_area_fields(
                self._rooms,
                ar.async_get(self.hass).async_list_areas(),
                configured_mappings,
                ignored_rooms,
            )
            if user_input is not None:
                mappings = dict(configured_mappings)
                mappings.update(_room_area_mappings(fields, user_input))
                return self.async_create_entry(
                    data={
                        **self.config_entry.options,
                        CONF_ROOM_AREAS: mappings,
                        CONF_IGNORED_ROOMS: _ignored_room_keys(
                            fields,
                            user_input,
                            ignored_rooms,
                        ),
                    }
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_room_area_schema(fields),
            errors=errors,
        )
