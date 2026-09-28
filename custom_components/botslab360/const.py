"""Constants for the Botslab 360 integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "botslab360"

CONF_Q = "q"
CONF_T = "t"

MANUFACTURER = "Botslab / 360"
PLATFORMS = (Platform.VACUUM, Platform.SENSOR)
UPDATE_INTERVAL = timedelta(seconds=60)
