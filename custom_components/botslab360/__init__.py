"""The Botslab 360 integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from botslab360 import (
    AuthBackend,
    AuthenticationError,
    Botslab360Client,
    DeviceIdentity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUTH_BACKEND,
    CONF_DEVICE_IDENTITY,
    CONF_IDENTITY_ANDROID_ID,
    CONF_IDENTITY_M2,
    CONF_IDENTITY_MID,
    CONF_Q,
    CONF_T,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import Botslab360Coordinator
from .map import Botslab360MapCache
from .services import async_register_services


@dataclass(slots=True)
class Botslab360RuntimeData:
    """Runtime data for a Botslab 360 config entry."""

    client: Botslab360Client
    coordinator: Botslab360Coordinator
    map_cache: Botslab360MapCache


type Botslab360ConfigEntry = ConfigEntry[Botslab360RuntimeData]


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


async def async_setup_entry(
    hass: HomeAssistant, entry: Botslab360ConfigEntry
) -> bool:
    """Set up Botslab 360 from a config entry."""

    try:
        client = create_client_from_entry_data(entry.data)
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

    entry.runtime_data = Botslab360RuntimeData(
        client,
        coordinator,
        Botslab360MapCache(hass, client),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: Botslab360ConfigEntry
) -> bool:
    """Unload a Botslab 360 config entry."""

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    entry.runtime_data.map_cache.clear()
    await entry.runtime_data.client.close()
    return True
