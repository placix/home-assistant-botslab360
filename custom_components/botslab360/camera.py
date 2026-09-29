"""Rendered room-map camera platform for Botslab 360."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from botslab360 import ApiError, AuthenticationError, Device

from . import Botslab360ConfigEntry
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a rendered room-map camera for each robot."""

    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        Botslab360MapCamera(entry, coordinator, device)
        for device in coordinator.devices.values()
    )


class Botslab360MapCamera(Botslab360Entity, Camera):
    """A cached polygon map, not a physical camera or live stream."""

    _attr_content_type = "image/png"
    _attr_should_poll = False
    _attr_translation_key = "map"

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        device: Device,
    ) -> None:
        """Initialize a room-map camera."""

        Botslab360Entity.__init__(self, coordinator, device)
        Camera.__init__(self)
        self._config_entry = entry
        self._map_cache = entry.runtime_data.map_cache
        self._attr_unique_id = f"{device.id}_map"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose calibration and compact card-oriented room metadata."""

        if (rendered := self._map_cache.get(self.device.id)) is None:
            return {
                "calibration_points": [],
                "rooms": [],
                "predefined_selections": [],
            }
        return {
            "calibration_points": list(rendered.calibration_points),
            "rooms": list(rendered.rooms),
            "predefined_selections": list(rendered.predefined_selections),
        }

    async def _async_refresh_map(self) -> None:
        try:
            await self._map_cache.async_refresh(self.device)
        except AuthenticationError:
            self._config_entry.async_start_reauth(self.hass)
            _LOGGER.warning("Authentication failed while refreshing the room map")
        except ApiError as err:
            _LOGGER.warning("Unable to refresh the room map: %s", err)
        else:
            if self.entity_id is not None:
                self.async_write_ha_state()

    async def async_update(self) -> None:
        """Refresh the map only when explicitly requested by Home Assistant."""

        await self._async_refresh_map()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the cached PNG, fetching room data on first use."""

        if self._map_cache.get(self.device.id) is None:
            await self._async_refresh_map()
        rendered = self._map_cache.get(self.device.id)
        return rendered.image if rendered is not None else None
