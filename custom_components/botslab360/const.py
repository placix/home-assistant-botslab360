"""Constants for the Botslab 360 integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "botslab360"
DATA_AUTHENTICATED_CLIENTS = "authenticated_clients"
DATA_AUTHENTICATED_NETWORK_MACS = "authenticated_network_macs"

CONF_AUTH_BACKEND = "backend"
CONF_CACHED_Q = "cached_q"
CONF_CACHED_T = "cached_t"
CONF_DEVICE_IDENTITY = "device_identity"
CONF_IDENTITY_ANDROID_ID = "android_id"
CONF_IDENTITY_M2 = "m2"
CONF_IDENTITY_MID = "mid"
CONF_IGNORED_ROOMS = "ignored_rooms"
CONF_Q = "q"
CONF_ROOM_AREAS = "room_areas"
CONF_ROOM_PREFERENCES = "room_preferences"
CONF_T = "t"

MANUFACTURER = "Botslab / 360"
PLATFORMS = (
    Platform.VACUUM,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SWITCH,
)
UPDATE_INTERVAL = timedelta(seconds=60)

CONF_CLEAN_TIMES = "clean_times"
CONF_FAN_MODE = "fan_mode"
LEGACY_CONF_CLEANING_MODE = "mode"
CONF_ROOM_IDS = "room_ids"
CONF_WATER_PUMP = "water_pump"
SERVICE_CLEAN_ROOMS = "clean_rooms"
