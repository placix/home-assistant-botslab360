"""Data coordinator for Botslab 360."""

from __future__ import annotations

import logging

from botslab360 import (
    ApiError,
    AuthenticationError,
    Botslab360Client,
    Device,
    RobotStatus,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class Botslab360Coordinator(DataUpdateCoordinator[dict[str, RobotStatus]]):
    """Coordinate device discovery and status polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Botslab360Client,
    ) -> None:
        """Initialize the coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.devices: dict[str, Device] = {}

    async def _async_setup(self) -> None:
        """Authenticate and discover the account's devices."""

        try:
            if self.client.session is None:
                await self.client.authenticate()
            devices = await self.client.get_devices()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except ApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err

        self.devices = {device.id: device for device in devices}

    async def _async_update_data(self) -> dict[str, RobotStatus]:
        """Fetch current status for every discovered device."""

        try:
            return {
                device_id: await self.client.get_status(device_id)
                for device_id in self.devices
            }
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except ApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err
