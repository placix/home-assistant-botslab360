"""Actions provided by the Botslab 360 integration."""

from __future__ import annotations

import voluptuous as vol

from botslab360 import (
    ROOM_CLEAN_TIMES,
    ApiError,
    AuthenticationError,
    RoomCleaningSettings,
    RoomFanMode,
    RoomWaterLevel,
)
from homeassistant.components.vacuum import DOMAIN as VACUUM_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_CLEAN_TIMES,
    CONF_FAN_MODE,
    CONF_ROOM_IDS,
    CONF_WATER_PUMP,
    DOMAIN,
    SERVICE_CLEAN_ROOMS,
)


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise vol.Invalid("value must be an integer")
    return value


def _single_entity_id(value: object) -> str:
    entity_ids = cv.entity_ids(value)
    if len(entity_ids) != 1:
        raise vol.Invalid("exactly one entity must be targeted")
    return entity_ids[0]


CLEAN_ROOMS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): _single_entity_id,
        vol.Required(CONF_ROOM_IDS): vol.All(
            cv.ensure_list,
            [_strict_int],
            vol.Length(min=1),
        ),
        vol.Optional(CONF_CLEAN_TIMES): vol.All(
            _strict_int, vol.In(ROOM_CLEAN_TIMES)
        ),
        vol.Optional(CONF_FAN_MODE): vol.In(
            [mode.value for mode in RoomFanMode]
        ),
        vol.Optional(CONF_WATER_PUMP): vol.All(
            _strict_int,
            vol.In([level.value for level in RoomWaterLevel]),
        ),
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration actions once during domain setup."""

    async def async_clean_rooms(call: ServiceCall) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        registry_entry = er.async_get(hass).async_get(entity_id)
        if (
            registry_entry is None
            or entity_id.split(".", 1)[0] != VACUUM_DOMAIN
            or registry_entry.platform != DOMAIN
            or registry_entry.config_entry_id is None
        ):
            raise ServiceValidationError(
                "Exactly one Botslab 360 vacuum must be targeted"
            )
        config_entry = hass.config_entries.async_get_entry(
            registry_entry.config_entry_id
        )
        if config_entry is None or config_entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("The targeted vacuum is not loaded")
        runtime = config_entry.runtime_data
        device = runtime.coordinator.devices.get(registry_entry.unique_id)
        if device is None:
            raise ServiceValidationError("The targeted vacuum is not loaded")

        room_ids = call.data[CONF_ROOM_IDS]
        clean_times = call.data.get(CONF_CLEAN_TIMES)
        fan_mode = call.data.get(CONF_FAN_MODE)
        water_pump = call.data.get(CONF_WATER_PUMP)
        settings = RoomCleaningSettings(
            clean_times=clean_times,
            fan_mode=fan_mode,
            water_pump=water_pump,
        )
        room_settings = (
            {room_id: settings for room_id in room_ids}
            if any(
                value is not None
                for value in (clean_times, fan_mode, water_pump)
            )
            else None
        )
        try:
            await runtime.client.clean_rooms(
                device,
                room_ids,
                room_settings=room_settings,
            )
        except AuthenticationError as err:
            config_entry.async_start_reauth(hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except ApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
            ) from err

        await runtime.coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAN_ROOMS,
        async_clean_rooms,
        schema=CLEAN_ROOMS_SCHEMA,
    )
