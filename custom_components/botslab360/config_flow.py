"""Config flow for Botslab 360."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, replace
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    AreaSelector,
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from botslab360 import (
    ApiError,
    AuthBackend,
    AuthenticationError,
    Botslab360Client,
    CaptchaChallenge,
    CaptchaRequired,
    Device,
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
from .entity import async_get_or_create_robot_device
from .network import (
    RuntimeRobotCandidate,
    async_get_unverified_candidate,
    async_mac_registered,
    async_match_runtime_robot,
    normalize_mac,
)

CONF_CAPTCHA_CODE = "captcha_code"

_LOGGER = logging.getLogger(__name__)


class NoDevicesError(Exception):
    """Raised when an account contains no supported devices."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validated account details needed by the config flow."""

    account_fingerprint: str
    title: str
    devices: tuple[Device, ...]
    rooms: tuple[DiscoveredRoom, ...]
    network_macs: tuple[tuple[str, str], ...] = ()
    unverified_network_device_ids: tuple[str, ...] = ()


def _discovery_title(hostname: str) -> str:
    """Return a human-readable title for the narrow supported hostname."""

    if hostname.casefold() == "360_cleanrobot_x9":
        return "360 CleanRobot X9"
    return "360 robot vacuum"


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
    discovery_mac: str | None = None,
) -> ValidationResult:
    devices = await client.get_devices()
    if not devices:
        raise NoDevicesError
    verified_network_macs: dict[str, str] = {}
    unverified_network_device_ids: list[str] = []
    if discovery_mac is not None:
        matching_device_ids: list[str] = []
        for device in devices:
            try:
                network_info = await client.get_network_info(device)
            except (AuthenticationError, ApiError, TimeoutError, OSError) as err:
                _LOGGER.debug(
                    "Could not verify network identity for account robot %s: %s",
                    device.id,
                    type(err).__name__,
                )
                unverified_network_device_ids.append(device.id)
                continue
            if (network_mac := normalize_mac(network_info.station_mac)) is None:
                unverified_network_device_ids.append(device.id)
                continue
            if network_mac == discovery_mac:
                matching_device_ids.append(device.id)
        if len(matching_device_ids) == 1:
            verified_device_id = matching_device_ids[0]
            verified_network_macs[verified_device_id] = discovery_mac
            _LOGGER.debug(
                "DHCP robot %s matched account robot %s",
                discovery_mac,
                verified_device_id,
            )
        else:
            _LOGGER.debug(
                "DHCP robot could not be uniquely matched to an account robot; "
                "continuing account setup without LAN association"
            )
    rooms: list[DiscoveredRoom] = []
    if include_rooms:
        for device in devices:
            rooms.extend(
                DiscoveredRoom(device, room) for room in await client.get_rooms(device)
            )
    return ValidationResult(
        account_fingerprint=client.account_fingerprint,
        title=devices[0].name or "Botslab 360",
        devices=tuple(devices),
        rooms=tuple(rooms),
        network_macs=tuple(verified_network_macs.items()),
        unverified_network_device_ids=tuple(unverified_network_device_ids),
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
        self._discovery_hostname: str | None = None
        self._discovery_mac: str | None = None
        self._discovery_ip: str | None = None
        self._discovery_confirmed = False
        self._pending_network_candidate: RuntimeRobotCandidate | None = None

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

    async def async_step_dhcp(
        self,
        discovery_info: DhcpServiceInfo,
    ) -> ConfigFlowResult:
        """Handle a robot discovered through DHCP."""

        hostname = discovery_info.hostname.casefold()
        network_mac = normalize_mac(discovery_info.macaddress)
        _LOGGER.info(
            "DHCP discovery received hostname=%s mac=%s ip=%s",
            hostname,
            network_mac,
            discovery_info.ip,
        )
        if network_mac is None:
            _LOGGER.info("DHCP discovery aborted: reason=invalid_discovery")
            return self.async_abort(reason="invalid_discovery")

        _LOGGER.info(
            "Attempting DHCP runtime matching: normalized_mac=%s",
            network_mac,
        )
        runtime_match = await async_match_runtime_robot(self.hass, network_mac)
        if runtime_match.matched:
            _LOGGER.info(
                "DHCP discovery suppressed after runtime matching: "
                "normalized_mac=%s reason=already_configured",
                network_mac,
            )
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(f"dhcp:{network_mac}")
        registered = async_mac_registered(self.hass, network_mac)
        _LOGGER.info(
            "DHCP post-unique-id registry check: normalized_mac=%s registered=%s",
            network_mac,
            registered,
        )
        if registered:
            _LOGGER.info(
                "DHCP discovery suppressed after registry re-check: "
                "normalized_mac=%s reason=already_configured",
                network_mac,
            )
            return self.async_abort(reason="already_configured")
        self._discovery_hostname = hostname
        self._discovery_mac = network_mac
        self._discovery_ip = discovery_info.ip
        self.context["title_placeholders"] = {
            "name": _discovery_title(hostname),
        }
        if len(runtime_match.unverified_candidates) == 1:
            self._pending_network_candidate = runtime_match.unverified_candidates[0]
            return await self.async_step_dhcp_bind_existing()
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm discovery before entering account credentials."""

        if self._discovery_mac is None:
            _LOGGER.info("DHCP confirmation aborted: reason=invalid_discovery")
            return self.async_abort(reason="invalid_discovery")
        registered = async_mac_registered(self.hass, self._discovery_mac)
        _LOGGER.info(
            "DHCP confirmation registry check: normalized_mac=%s registered=%s",
            self._discovery_mac,
            registered,
        )
        if registered:
            _LOGGER.info(
                "DHCP confirmation suppressed after registry check: "
                "normalized_mac=%s reason=already_configured",
                self._discovery_mac,
            )
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            _LOGGER.info(
                "Attempting DHCP runtime matching during confirmation: "
                "normalized_mac=%s",
                self._discovery_mac,
            )
            runtime_match = await async_match_runtime_robot(
                self.hass,
                self._discovery_mac,
            )
            if runtime_match.matched:
                _LOGGER.info(
                    "DHCP confirmation suppressed after runtime matching: "
                    "normalized_mac=%s reason=already_configured",
                    self._discovery_mac,
                )
                return self.async_abort(reason="already_configured")
            if len(runtime_match.unverified_candidates) == 1:
                self._pending_network_candidate = runtime_match.unverified_candidates[0]
                return await self.async_step_dhcp_bind_existing()
            self._discovery_confirmed = True
            return await self.async_step_user()
        return self.async_show_form(
            step_id="dhcp_confirm",
            data_schema=vol.Schema({}),
        )

    async def async_step_dhcp_bind_existing(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm a one-time MAC association with one existing robot."""

        candidate = self._pending_network_candidate
        network_mac = self._discovery_mac
        if candidate is None or network_mac is None:
            return self.async_abort(reason="invalid_discovery")
        if user_input is None:
            return self.async_show_form(
                step_id="dhcp_bind_existing",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "robot_name": candidate.device.name or candidate.device.id,
                    "network_mac": network_mac,
                },
            )

        runtime_match = await async_match_runtime_robot(self.hass, network_mac)
        if runtime_match.matched:
            return self.async_abort(reason="already_configured")
        if len(runtime_match.unverified_candidates) != 1:
            return self.async_abort(reason="invalid_discovery")
        refreshed_candidate = runtime_match.unverified_candidates[0]
        if (
            refreshed_candidate.config_entry_id != candidate.config_entry_id
            or refreshed_candidate.device_registry_id != candidate.device_registry_id
        ):
            return self.async_abort(reason="invalid_discovery")
        candidate = refreshed_candidate
        if self.hass.config_entries.async_get_entry(candidate.config_entry_id) is None:
            return self.async_abort(reason="invalid_discovery")
        registry = dr.async_get(self.hass)
        current_candidate = async_get_unverified_candidate(
            registry,
            candidate.config_entry_id,
            candidate.device,
        )
        if (
            current_candidate is None
            or current_candidate.device_registry_id != candidate.device_registry_id
        ):
            return self.async_abort(reason="invalid_discovery")
        try:
            registry_device = async_get_or_create_robot_device(
                registry,
                candidate.config_entry_id,
                candidate.device,
                network_mac,
            )
        except dr.DeviceInfoError:
            return self.async_show_form(
                step_id="dhcp_bind_existing",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "robot_name": candidate.device.name or candidate.device.id,
                    "network_mac": network_mac,
                },
                errors={"base": "cannot_connect"},
            )
        if (
            dr.CONNECTION_NETWORK_MAC,
            network_mac,
        ) not in registry_device.connections:
            return self.async_abort(reason="invalid_discovery")
        _LOGGER.info(
            "User confirmed DHCP MAC association: config_entry_id=%s "
            "robot_id=%s normalized_mac=%s",
            candidate.config_entry_id,
            candidate.device.id,
            network_mac,
        )
        return self.async_abort(reason="already_configured")

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
                        discovery_mac=(
                            self._discovery_mac if not self._pending_reauth else None
                        ),
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
        verified_network_macs = (
            dict(validation.network_macs) if self._discovery_mac is not None else None
        )
        self._clear_pending_state()
        await async_store_authenticated_client(
            self.hass,
            validation.account_fingerprint,
            client,
            verified_network_macs,
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
            validation = await _async_discover(
                client,
                include_rooms=not reauth,
                discovery_mac=self._discovery_mac if not reauth else None,
            )
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

        fallback_device = None
        if (
            self._discovery_confirmed
            and self._discovery_mac is not None
            and not validation.network_macs
            and len(validation.devices) == 1
            and validation.unverified_network_device_ids == (validation.devices[0].id,)
            and not async_mac_registered(self.hass, self._discovery_mac)
        ):
            fallback_device = validation.devices[0]

        try:
            existing_entry = await self.async_set_unique_id(
                validation.account_fingerprint
            )
        except BaseException:
            await client.close()
            raise
        if existing_entry is not None:
            if self._discovery_mac is not None:
                network_macs = dict(validation.network_macs)
                for device in validation.devices:
                    verified_match = network_macs.get(device.id) == self._discovery_mac
                    fallback_match = (
                        fallback_device is not None
                        and device.id == fallback_device.id
                        and not async_mac_registered(
                            self.hass,
                            self._discovery_mac,
                        )
                        and async_get_unverified_candidate(
                            dr.async_get(self.hass),
                            existing_entry.entry_id,
                            device,
                        )
                        is not None
                    )
                    if verified_match or fallback_match:
                        async_get_or_create_robot_device(
                            dr.async_get(self.hass),
                            existing_entry.entry_id,
                            device,
                            self._discovery_mac,
                        )
                        break
            await client.close()
            return self.async_abort(reason="already_configured")
        if fallback_device is not None and self._discovery_mac is not None:
            validation = replace(
                validation,
                network_macs=((fallback_device.id, self._discovery_mac),),
            )
            _LOGGER.info(
                "Using user-confirmed DHCP MAC for single authenticated robot: "
                "robot_id=%s normalized_mac=%s",
                fallback_device.id,
                self._discovery_mac,
            )
        self._pending_client = client
        self._pending_challenge = None
        self._pending_data = entry_data
        self._pending_validation = validation
        self._pending_reauth = False
        return await self.async_step_room_areas()

    async def _async_return_to_origin(self, error: str) -> ConfigFlowResult:
        reauth = self._pending_reauth
        await self._async_clear_pending(clear_discovery=False)
        if reauth:
            return self._show_native_reauth_form({"base": error})
        return self._show_user_form({"base": error})

    async def _async_clear_pending(self, *, clear_discovery: bool = True) -> None:
        if self._pending_client is not None:
            await self._pending_client.close()
        self._clear_pending_state(clear_discovery=clear_discovery)

    def _clear_pending_state(self, *, clear_discovery: bool = True) -> None:
        """Forget transient captcha state after ownership is transferred."""

        self._pending_client = None
        self._pending_challenge = None
        self._pending_data = None
        self._pending_validation = None
        self._pending_reauth = False
        if clear_discovery:
            self._discovery_confirmed = False
            self._pending_network_candidate = None
            self._discovery_hostname = None
            self._discovery_mac = None
            self._discovery_ip = None

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
