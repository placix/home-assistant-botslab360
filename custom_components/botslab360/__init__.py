"""The Botslab 360 integration."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from botslab360 import (
    ApiError,
    AuthBackend,
    AuthenticationError,
    Botslab360Client,
    DeviceIdentity,
)

from .areas import DiscoveredRoom, PreparedRoom, async_prepare_room_devices
from .const import (
    CONF_AUTH_BACKEND,
    CONF_CACHED_Q,
    CONF_CACHED_T,
    CONF_DEVICE_IDENTITY,
    CONF_IDENTITY_ANDROID_ID,
    CONF_IDENTITY_M2,
    CONF_IDENTITY_MID,
    CONF_Q,
    CONF_T,
    DATA_AUTHENTICATED_CLIENTS,
    DATA_AUTHENTICATED_NETWORK_MACS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import Botslab360Coordinator
from .entity import async_get_or_create_robot_device
from .map import Botslab360MapCache
from .network import async_abort_dhcp_flows, normalize_mac
from .room_jobs import RoomJobState
from .room_preferences import remove_legacy_cleaning_mode_preferences
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Botslab360RuntimeData:
    """Runtime data for a Botslab 360 config entry."""

    client: Botslab360Client
    coordinator: Botslab360Coordinator
    map_cache: Botslab360MapCache
    rooms: tuple[PreparedRoom, ...] = ()
    room_jobs: RoomJobState = field(default_factory=RoomJobState)


type Botslab360ConfigEntry = ConfigEntry[Botslab360RuntimeData]


async def async_store_authenticated_client(
    hass: HomeAssistant,
    account_fingerprint: str,
    client: Botslab360Client,
    network_macs: Mapping[str, str] | None = None,
) -> None:
    """Store an authenticated client for the next config entry setup."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    clients: dict[str, Botslab360Client] = domain_data.setdefault(
        DATA_AUTHENTICATED_CLIENTS, {}
    )
    previous = clients.get(account_fingerprint)
    if previous is not None and previous is not client:
        await previous.close()
    clients[account_fingerprint] = client
    if network_macs is not None:
        authenticated_network_macs: dict[str, dict[str, str]] = domain_data.setdefault(
            DATA_AUTHENTICATED_NETWORK_MACS, {}
        )
        authenticated_network_macs[account_fingerprint] = dict(network_macs)


def take_authenticated_client(
    hass: HomeAssistant,
    account_fingerprint: str | None,
) -> Botslab360Client | None:
    """Consume a client handed off by a successful config flow."""

    if account_fingerprint is None:
        return None
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        return None
    clients: dict[str, Botslab360Client] | None = domain_data.get(
        DATA_AUTHENTICATED_CLIENTS
    )
    if clients is None:
        return None
    return clients.pop(account_fingerprint, None)


def take_authenticated_network_macs(
    hass: HomeAssistant,
    account_fingerprint: str | None,
) -> dict[str, str] | None:
    """Consume verified robot MAC addresses handed off by a config flow."""

    if account_fingerprint is None or not (domain_data := hass.data.get(DOMAIN)):
        return None
    network_macs: dict[str, dict[str, str]] | None = domain_data.get(
        DATA_AUTHENTICATED_NETWORK_MACS
    )
    if network_macs is None:
        return None
    return network_macs.pop(account_fingerprint, None)


async def async_discard_authenticated_client(
    hass: HomeAssistant,
    account_fingerprint: str,
    client: Botslab360Client,
) -> None:
    """Close a handed-off client if it is still awaiting setup."""

    domain_data = hass.data.get(DOMAIN)
    clients = (
        domain_data.get(DATA_AUTHENTICATED_CLIENTS) if domain_data is not None else None
    )
    if clients is not None and clients.get(account_fingerprint) is client:
        clients.pop(account_fingerprint)
        network_macs = domain_data.get(DATA_AUTHENTICATED_NETWORK_MACS)
        if network_macs is not None:
            network_macs.pop(account_fingerprint, None)
        await client.close()


async def _async_register_robot_network_macs(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    client: Botslab360Client,
    network_macs: Mapping[str, str] | None,
) -> None:
    """Best-effort register physical robot MAC connections."""

    registry = dr.async_get(hass)
    devices = tuple(entry.runtime_data.coordinator.devices.values())
    _LOGGER.debug(
        "Starting robot network identity registration: config_entry_id=%s "
        "robots=%d handed_off_network_macs=%s",
        entry.entry_id,
        len(devices),
        network_macs is not None,
    )
    candidate_macs: dict[str, str | None] = {}
    for device in devices:
        network_mac = network_macs.get(device.id) if network_macs is not None else None
        normalized_mac = normalize_mac(network_mac)
        if network_macs is None:
            try:
                network_info = await client.get_network_info(device)
            except (AuthenticationError, ApiError, TimeoutError, OSError) as err:
                _LOGGER.debug(
                    "Could not retrieve network information for %s: %s",
                    device.id,
                    type(err).__name__,
                )
            else:
                network_mac = network_info.station_mac
                normalized_mac = normalize_mac(network_mac)
                _LOGGER.debug(
                    "Robot network information received: robot_id=%s "
                    "station_ip=%s station_mac=%s normalized_mac=%s",
                    device.id,
                    network_info.station_ip,
                    network_mac,
                    normalized_mac,
                )
        else:
            normalized_mac = normalize_mac(network_mac)
            _LOGGER.debug(
                "Using handed-off robot network identity: robot_id=%s "
                "station_mac=%s normalized_mac=%s",
                device.id,
                network_mac,
                normalized_mac,
            )
        if not network_mac:
            _LOGGER.debug(
                "Robot network identity has no station MAC: robot_id=%s",
                device.id,
            )
        elif normalized_mac is None:
            _LOGGER.debug(
                "Robot station MAC was rejected during normalization: "
                "robot_id=%s station_mac=%s",
                device.id,
                network_mac,
            )
        candidate_macs[device.id] = normalized_mac

    mac_counts = Counter(
        network_mac
        for network_mac in candidate_macs.values()
        if network_mac is not None
    )
    registered_macs: set[str] = set()
    for device in devices:
        network_mac = candidate_macs[device.id]
        if network_mac is not None and mac_counts[network_mac] > 1:
            _LOGGER.debug(
                "Robot network identity is ambiguous: robot_id=%s "
                "normalized_mac=%s matches=%d",
                device.id,
                network_mac,
                mac_counts[network_mac],
            )
            network_mac = None
        try:
            if network_mac is not None:
                _LOGGER.debug(
                    "Registering robot Device Registry MAC connection: "
                    "robot_id=%s normalized_mac=%s",
                    device.id,
                    network_mac,
                )
            registry_device = async_get_or_create_robot_device(
                registry,
                entry.entry_id,
                device,
                network_mac,
            )
            if network_mac is not None:
                connection_present = (
                    dr.CONNECTION_NETWORK_MAC,
                    network_mac,
                ) in registry_device.connections
                _LOGGER.debug(
                    "Robot network identity registration succeeded: "
                    "robot_id=%s device_registry_id=%s normalized_mac=%s "
                    "expected_connection_present=%s",
                    device.id,
                    registry_device.id,
                    network_mac,
                    connection_present,
                )
                registered_macs.add(network_mac)
        except dr.DeviceInfoError as err:
            _LOGGER.warning(
                "Robot network identity registration raised DeviceInfoError "
                "for robot %s: %s",
                device.id,
                err,
            )
    for network_mac in registered_macs:
        async_abort_dhcp_flows(hass, network_mac)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up domain-level Botslab 360 actions."""

    async_register_services(hass)
    return True


def create_client_from_entry_data(
    data: Mapping[str, Any],
) -> Botslab360Client:
    """Create a library client for legacy or native entry data."""

    if CONF_Q in data and CONF_T in data:
        return Botslab360Client(data[CONF_Q], data[CONF_T])
    if CONF_CACHED_Q in data and CONF_CACHED_T in data:
        return Botslab360Client(data[CONF_CACHED_Q], data[CONF_CACHED_T])

    return create_native_client_from_entry_data(data)


def create_native_client_from_entry_data(
    data: Mapping[str, Any],
) -> Botslab360Client:
    """Create a native credential client while retaining its device identity."""

    identity_data = data[CONF_DEVICE_IDENTITY]
    identity = DeviceIdentity(
        mid=identity_data[CONF_IDENTITY_MID],
        android_id=identity_data[CONF_IDENTITY_ANDROID_ID],
        m2=identity_data[CONF_IDENTITY_M2],
    )
    return Botslab360Client.from_credentials(
        email=data[CONF_EMAIL],
        password=data[CONF_PASSWORD],
        backend=AuthBackend(data[CONF_AUTH_BACKEND]),
        device_identity=identity,
    )


def _has_native_cached_credentials(data: Mapping[str, Any]) -> bool:
    """Return whether a native entry has a complete reusable Q/T cache."""

    return (
        CONF_EMAIL in data
        and CONF_PASSWORD in data
        and CONF_CACHED_Q in data
        and CONF_CACHED_T in data
    )


def _async_update_cached_credentials(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    client: Botslab360Client,
) -> None:
    """Persist changed reusable Q/T credentials for native entries only."""

    if CONF_EMAIL not in entry.data or (credentials := client.credentials) is None:
        return
    if (
        entry.data.get(CONF_CACHED_Q) == credentials.q
        and entry.data.get(CONF_CACHED_T) == credentials.t
    ):
        return
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CACHED_Q: credentials.q,
            CONF_CACHED_T: credentials.t,
        },
    )


async def async_setup_entry(hass: HomeAssistant, entry: Botslab360ConfigEntry) -> bool:
    """Set up Botslab 360 from a config entry."""

    client = take_authenticated_client(hass, entry.unique_id)
    authenticated_network_macs = take_authenticated_network_macs(hass, entry.unique_id)
    used_handoff = client is not None
    used_native_cache = False
    if client is None:
        try:
            client = create_client_from_entry_data(entry.data)
        except (AuthenticationError, KeyError, ValueError) as err:
            if not _has_native_cached_credentials(entry.data):
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="invalid_auth",
                ) from err
            try:
                client = create_native_client_from_entry_data(entry.data)
            except (AuthenticationError, KeyError, ValueError) as fallback_err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="invalid_auth",
                ) from fallback_err
        else:
            used_native_cache = _has_native_cached_credentials(entry.data)

    coordinator = Botslab360Coordinator(hass, entry, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        if used_handoff or not used_native_cache:
            await client.close()
            raise
        await client.close()
        try:
            client = create_native_client_from_entry_data(entry.data)
        except (AuthenticationError, KeyError, ValueError) as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        coordinator = Botslab360Coordinator(hass, entry, client)
        try:
            await coordinator.async_config_entry_first_refresh()
        except BaseException:
            await client.close()
            raise
    except BaseException:
        await client.close()
        raise

    _async_update_cached_credentials(hass, entry, client)

    entry.runtime_data = Botslab360RuntimeData(
        client,
        coordinator,
        Botslab360MapCache(hass, client),
    )
    await _async_register_robot_network_macs(
        hass,
        entry,
        client,
        authenticated_network_macs,
    )

    discovered_rooms: list[DiscoveredRoom] = []
    try:
        for device in coordinator.devices.values():
            discovered_rooms.extend(
                DiscoveredRoom(device, room) for room in await client.get_rooms(device)
            )
    except AuthenticationError as err:
        await client.close()
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
        ) from err
    except (ApiError, TimeoutError, OSError) as err:
        await client.close()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err

    normalized_options = remove_legacy_cleaning_mode_preferences(entry.options)
    if normalized_options != dict(entry.options):
        hass.config_entries.async_update_entry(entry, options=normalized_options)

    prepared_rooms = async_prepare_room_devices(
        hass,
        entry.entry_id,
        entry.options,
        discovered_rooms,
    )

    entry.runtime_data.rooms = prepared_rooms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Botslab360ConfigEntry) -> bool:
    """Unload a Botslab 360 config entry."""

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    entry.runtime_data.map_cache.clear()
    await entry.runtime_data.client.close()
    return True
