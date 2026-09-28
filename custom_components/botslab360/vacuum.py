"""Vacuum platform for Botslab 360."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from botslab360 import ApiError, AuthenticationError, Device, RobotStatus
from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Botslab360ConfigEntry
from .const import DOMAIN
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity

SUPPORTED_FEATURES = (
    VacuumEntityFeature.STATE
    | VacuumEntityFeature.START
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.LOCATE
)

STATE_MAP = {
    "sweep": VacuumActivity.CLEANING,
    "mop": VacuumActivity.CLEANING,
    "charge": VacuumActivity.DOCKED,
    "fullcharge": VacuumActivity.DOCKED,
    "idle": VacuumActivity.IDLE,
    "dormant": VacuumActivity.IDLE,
    "pause": VacuumActivity.PAUSED,
    "backcharge": VacuumActivity.RETURNING,
    "fault": VacuumActivity.ERROR,
}


def vacuum_activity(status: RobotStatus | None) -> VacuumActivity | None:
    """Map a Botslab status to a Home Assistant vacuum activity."""

    if status is None:
        return None
    if status.error_code != 0:
        return VacuumActivity.ERROR
    if not isinstance(status.state, str):
        return None
    return STATE_MAP.get(status.state.casefold())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Botslab 360 vacuum entities."""

    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        Botslab360Vacuum(entry, coordinator, device)
        for device in coordinator.devices.values()
    )


class Botslab360Vacuum(Botslab360Entity, StateVacuumEntity):
    """Representation of a Botslab 360 robot vacuum."""

    _attr_name = None
    _attr_supported_features = SUPPORTED_FEATURES

    def __init__(
        self,
        entry: Botslab360ConfigEntry,
        coordinator: Botslab360Coordinator,
        device: Device,
    ) -> None:
        """Initialize the vacuum entity."""

        super().__init__(coordinator, device)
        self._config_entry = entry
        self._attr_unique_id = device.id

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the current vacuum activity."""

        return vacuum_activity(self.robot_status)

    async def _async_command(
        self, command: Callable[[str], Awaitable[None]]
    ) -> None:
        """Run a library command and refresh status after success."""

        try:
            await command(self.device.id)
        except AuthenticationError as err:
            self._config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except ApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
            ) from err

        await self.coordinator.async_request_refresh()

    async def async_start(self) -> None:
        """Start or resume cleaning."""

        status = self.robot_status
        command = (
            self.coordinator.client.resume
            if status is not None
            and isinstance(status.state, str)
            and status.state.casefold() == "pause"
            else self.coordinator.client.start_cleaning
        )
        await self._async_command(command)

    async def async_pause(self) -> None:
        """Pause cleaning."""

        await self._async_command(self.coordinator.client.pause)

    async def async_return_to_base(self, **kwargs: object) -> None:
        """Return the robot to its dock."""

        await self._async_command(self.coordinator.client.return_to_dock)

    async def async_locate(self, **kwargs: object) -> None:
        """Ask the robot to identify itself audibly."""

        await self._async_command(self.coordinator.client.locate)
