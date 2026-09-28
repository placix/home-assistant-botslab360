"""The Botslab 360 integration."""

from __future__ import annotations

from dataclasses import dataclass

from botslab360 import AuthenticationError, Botslab360Client
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import CONF_Q, CONF_T, DOMAIN, PLATFORMS
from .coordinator import Botslab360Coordinator


@dataclass(slots=True)
class Botslab360RuntimeData:
    """Runtime data for a Botslab 360 config entry."""

    client: Botslab360Client
    coordinator: Botslab360Coordinator


type Botslab360ConfigEntry = ConfigEntry[Botslab360RuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: Botslab360ConfigEntry
) -> bool:
    """Set up Botslab 360 from a config entry."""

    try:
        client = Botslab360Client(entry.data[CONF_Q], entry.data[CONF_T])
    except AuthenticationError as err:
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

    entry.runtime_data = Botslab360RuntimeData(client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: Botslab360ConfigEntry
) -> bool:
    """Unload a Botslab 360 config entry."""

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.client.close()
    return True
