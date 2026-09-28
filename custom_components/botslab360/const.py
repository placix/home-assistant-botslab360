"""Constants for the Botslab 360 integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "botslab360"

CONF_AUTH_BACKEND = "backend"
CONF_DEVICE_IDENTITY = "device_identity"
CONF_IDENTITY_ANDROID_ID = "android_id"
CONF_IDENTITY_M2 = "m2"
CONF_IDENTITY_MID = "mid"
CONF_Q = "q"
CONF_T = "t"

MANUFACTURER = "Botslab / 360"
PLATFORMS = (Platform.VACUUM, Platform.SENSOR, Platform.CAMERA)
UPDATE_INTERVAL = timedelta(seconds=60)

CONF_CLEAN_TIMES = "clean_times"
CONF_FAN_MODE = "fan_mode"
CONF_ROOM_IDS = "room_ids"
CONF_WATER_PUMP = "water_pump"
SERVICE_CLEAN_ROOMS = "clean_rooms"
