"""Network identity helpers for Botslab 360 devices."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import SOURCE_DHCP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from botslab360 import ApiError, AuthenticationError, Device

from .const import DOMAIN
from .entity import async_get_or_create_robot_device

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeRobotCandidate:
    """An existing robot eligible for explicit DHCP MAC association."""

    config_entry_id: str
    device_registry_id: str
    device: Device


@dataclass(frozen=True, slots=True)
class RuntimeRobotMatchResult:
    """Result of verified and fallback runtime network matching."""

    matched: bool
    unverified_candidates: tuple[RuntimeRobotCandidate, ...] = ()


def normalize_mac(mac_address: str | None) -> str | None:
    """Return a normalized valid MAC address."""

    if not mac_address:
        return None
    normalized = dr.format_mac(mac_address)
    octets = normalized.split(":")
    if len(octets) != 6 or any(
        len(octet) != 2
        or any(character not in "0123456789abcdef" for character in octet)
        for octet in octets
    ):
        return None
    return normalized


@callback
def async_mac_registered(hass: HomeAssistant, network_mac: str) -> bool:
    """Return whether a Botslab entry already owns the network MAC."""

    registry = dr.async_get(hass)
    registered = any(
        registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, network_mac),
            entry.entry_id,
        )
        is not None
        for entry in hass.config_entries.async_entries(DOMAIN)
    )
    _LOGGER.debug(
        "Device Registry MAC lookup completed: normalized_mac=%s registered=%s",
        network_mac,
        registered,
    )
    return registered


@callback
def async_get_unverified_candidate(
    registry: dr.DeviceRegistry,
    config_entry_id: str,
    device: Device,
) -> RuntimeRobotCandidate | None:
    """Return a candidate only for an existing robot without a network MAC."""

    registry_device = registry.async_get_device_by_identifier(
        (DOMAIN, device.id),
        config_entry_id,
    )
    if registry_device is None or any(
        connection_type == dr.CONNECTION_NETWORK_MAC
        for connection_type, _ in registry_device.connections
    ):
        return None
    return RuntimeRobotCandidate(
        config_entry_id=config_entry_id,
        device_registry_id=registry_device.id,
        device=device,
    )


async def async_match_runtime_robot(
    hass: HomeAssistant,
    network_mac: str,
) -> RuntimeRobotMatchResult:
    """Best-effort match a DHCP MAC to one configured runtime robot."""

    registry_match = async_mac_registered(hass, network_mac)
    _LOGGER.debug(
        "DHCP runtime match pre-check: normalized_mac=%s registry_match=%s",
        network_mac,
        registry_match,
    )
    if registry_match:
        _LOGGER.debug(
            "DHCP runtime matching skipped because the MAC is already registered: "
            "normalized_mac=%s",
            network_mac,
        )
        return RuntimeRobotMatchResult(matched=True)

    _LOGGER.debug(
        "DHCP runtime matching started: normalized_mac=%s",
        network_mac,
    )
    matches: list[tuple[str, Device]] = []
    unverified_candidates: list[RuntimeRobotCandidate] = []
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            continue
        for device in runtime.coordinator.devices.values():
            try:
                network_info = await runtime.client.get_network_info(device)
            except (AuthenticationError, ApiError, TimeoutError, OSError) as err:
                _LOGGER.debug(
                    "Could not correlate DHCP identity for robot %s: %s",
                    device.id,
                    type(err).__name__,
                )
                if candidate := async_get_unverified_candidate(
                    registry,
                    entry.entry_id,
                    device,
                ):
                    unverified_candidates.append(candidate)
                continue
            normalized_robot_mac = normalize_mac(network_info.station_mac)
            _LOGGER.debug(
                "DHCP runtime robot network information received: robot_id=%s "
                "station_ip=%s station_mac=%s normalized_mac=%s",
                device.id,
                network_info.station_ip,
                network_info.station_mac,
                normalized_robot_mac,
            )
            if not network_info.station_mac:
                _LOGGER.debug(
                    "DHCP runtime robot has no station MAC: robot_id=%s",
                    device.id,
                )
            elif normalized_robot_mac is None:
                _LOGGER.debug(
                    "DHCP runtime robot station MAC was rejected during "
                    "normalization: robot_id=%s station_mac=%s",
                    device.id,
                    network_info.station_mac,
                )
            if normalized_robot_mac is None and (
                candidate := async_get_unverified_candidate(
                    registry,
                    entry.entry_id,
                    device,
                )
            ):
                unverified_candidates.append(candidate)
            if normalized_robot_mac == network_mac:
                matches.append((entry.entry_id, device))

    _LOGGER.debug(
        "DHCP runtime matching completed: normalized_mac=%s matches=%d "
        "unverified_candidates=%d",
        network_mac,
        len(matches),
        len(unverified_candidates),
    )

    # Existing-entry setup may have registered the MAC while lookups awaited.
    if async_mac_registered(hass, network_mac):
        _LOGGER.debug(
            "DHCP runtime match became registered while lookups were pending: "
            "normalized_mac=%s",
            network_mac,
        )
        return RuntimeRobotMatchResult(matched=True)
    if len(matches) != 1:
        if len(matches) > 1:
            _LOGGER.debug(
                "Multiple configured Botslab robots reported DHCP MAC %s; "
                "not assigning the connection",
                network_mac,
            )
        return RuntimeRobotMatchResult(
            matched=False,
            unverified_candidates=(tuple(unverified_candidates) if not matches else ()),
        )

    entry_id, device = matches[0]
    try:
        _LOGGER.debug(
            "Registering runtime-matched robot Device Registry MAC connection: "
            "config_entry_id=%s robot_id=%s normalized_mac=%s",
            entry_id,
            device.id,
            network_mac,
        )
        registry_device = async_get_or_create_robot_device(
            dr.async_get(hass),
            entry_id,
            device,
            network_mac,
        )
        connection_present = (
            dr.CONNECTION_NETWORK_MAC,
            network_mac,
        ) in registry_device.connections
        _LOGGER.debug(
            "Runtime-matched robot network identity registration succeeded: "
            "robot_id=%s device_registry_id=%s normalized_mac=%s "
            "expected_connection_present=%s",
            device.id,
            registry_device.id,
            network_mac,
            connection_present,
        )
    except dr.DeviceInfoError as err:
        _LOGGER.warning(
            "Runtime robot network identity registration raised DeviceInfoError "
            "for robot %s: %s",
            device.id,
            err,
        )
    return RuntimeRobotMatchResult(matched=True)


@callback
def async_abort_dhcp_flows(hass: HomeAssistant, network_mac: str) -> None:
    """Remove stale DHCP flows after an existing robot learns its MAC."""

    unique_id = f"dhcp:{network_mac}"
    for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        context = flow["context"]
        if (
            context.get("source") == SOURCE_DHCP
            and context.get("unique_id") == unique_id
        ):
            hass.config_entries.flow.async_abort(flow["flow_id"])
