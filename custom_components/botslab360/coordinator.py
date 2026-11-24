"""Data coordinator for Botslab 360."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from botslab360 import (
    ApiError,
    AuthenticationError,
    Botslab360Client,
    Device,
    RobotStatus,
)

from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

# Only fixed messages from botslab360 0.7.5 may be logged. Exception causes can
# contain request URLs, credentials or response bodies; never log raw tracebacks.
_SAFE_ERROR_MESSAGES = (
    {
        "Timed out waiting for robot status",
        "Timed out waiting for push registration",
        "Push connection failed",
        "Push connection closed",
        "Push client is already connected",
        "Push client is not connected",
        "Push client is not ready",
        "Push registration was not confirmed",
        "Push acknowledgement failed",
        "Push transport version is invalid",
        "Push key is missing",
        "Encrypted push data is missing",
        "Push data could not be decrypted",
        "Push data could not be decoded",
        "Push envelope is invalid",
        "Push envelope is missing data",
        "Push event is invalid",
        "Decrypted push data is invalid JSON",
        "Decrypted push event is invalid",
        "Smart Home session has expired",
        "Smart Home response contains an invalid errno",
        "Smart Home API rejected the login",
        "Smart Home API rejected robot status request",
        "Smart Home API rejected device discovery",
        "Smart Home login request failed",
        "Smart Home login response is missing session data",
        "Smart Home login response is missing sid",
        "Smart Home login response is missing pushKey",
        "Device discovery request failed",
        "Device list contains an invalid device",
        "Device list contains a device without an id",
        "Device list contains invalid device metadata",
        "Device list contains an invalid online state",
    }
    | {
        f"{operation} {result}"
        for operation in (
            "Robot status request",
            "Device discovery",
            "Smart Home login",
        )
        for result in (
            "failed",
            "returned an HTTP error",
            "returned invalid JSON",
            "returned an invalid response",
        )
    }
    | {
        f"Status field {field} is invalid"
        for field in (
            "mode",
            "workNoisy",
            "online",
            "errorState",
            "pos",
            "elecReal",
            "cleanArea",
            "cleanTime",
            "mopStatus",
            "allArea",
            "allTime",
            "subMode",
            "lastSubMode",
            "phi",
            "timerStatus",
        )
    }
    | {
        f"Push {field} is invalid{suffix}"
        for field in ("protocol", "status data")
        for suffix in ("", " JSON")
    }
)
_SAFE_ERROR_PHASES = {
    "api",
    "authentication",
    "http",
    "transport",
    "json",
    "protocol",
    "response-validation",
    "timeout",
    "decryption",
    "quc-login",
    "captcha",
}


def _log_api_error(stage: str, error: ApiError) -> None:
    """Describe a failure without exposing arbitrary exception text or context."""

    if not _LOGGER.isEnabledFor(logging.DEBUG):
        return
    message = error.args[0] if len(error.args) == 1 else None
    safe_message = (
        message
        if type(message) is str and message in _SAFE_ERROR_MESSAGES
        else "[redacted]"
    )
    phase = error.phase
    safe_phase = (
        phase if type(phase) is str and phase in _SAFE_ERROR_PHASES else "unknown"
    )
    codes = [
        value if type(value) is int else None
        for value in (
            error.status_code,
            error.errno,
            error.response_errno,
            error.error_code,
        )
    ]
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(type(current).__name__)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    _LOGGER.debug(
        "Botslab 360 %s failed: phase=%s status_code=%s errno=%s "
        "response_errno=%s error_code=%s message=%s exception_chain=%s",
        stage,
        safe_phase,
        *codes,
        safe_message,
        " -> ".join(chain),
    )


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
            _log_api_error("setup", err)
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
            _log_api_error("update", err)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err
