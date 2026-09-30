"""Network identity helpers for Botslab 360 devices."""

from __future__ import annotations

import logging

from homeassistant.config_entries import SOURCE_DHCP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from botslab360 import ApiError, AuthenticationError, Device

from .const import DOMAIN
from .entity import async_get_or_create_robot_device

_LOGGER = logging.getLogger(__name__)


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
    return any(
        registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, network_mac),
            entry.entry_id,
        )
        is not None
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def async_match_runtime_robot(hass: HomeAssistant, network_mac: str) -> bool:
    """Best-effort match a DHCP MAC to one configured runtime robot."""

    if async_mac_registered(hass, network_mac):
        return True

    matches: list[tuple[str, Device]] = []
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
                continue
            if normalize_mac(network_info.station_mac) == network_mac:
                matches.append((entry.entry_id, device))

    # Existing-entry setup may have registered the MAC while lookups awaited.
    if async_mac_registered(hass, network_mac):
        return True
    if len(matches) != 1:
        if len(matches) > 1:
            _LOGGER.warning(
                "Multiple configured Botslab robots reported DHCP MAC %s; "
                "not assigning the connection",
                network_mac,
            )
        return False

    entry_id, device = matches[0]
    try:
        async_get_or_create_robot_device(
            dr.async_get(hass),
            entry_id,
            device,
            network_mac,
        )
    except dr.DeviceInfoError as err:
        _LOGGER.warning(
            "Could not register DHCP identity for robot %s: %s",
            device.id,
            err,
        )
    return True


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
