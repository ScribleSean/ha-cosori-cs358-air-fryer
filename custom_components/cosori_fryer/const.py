"""Constants for the COSORI CS358 air fryer integration."""

from datetime import timedelta

DOMAIN = "cosori_fryer"

CONF_ALLOW_UNVERIFIED = "allow_unverified"
CONF_NOTIFY_SERVICE = "notify_service"

POLL_ACTIVE = timedelta(seconds=30)
POLL_IDLE = timedelta(minutes=5)
# Keep polling fast for a while after activity, so "basket back in" is noticed.
ACTIVE_LINGER_SECONDS = 600

DEFAULT_TEMP_F = 350
DEFAULT_MINUTES = 10

EVENT_PREHEAT_DONE = "cosori_fryer_preheat_done"
EVENT_COOK_DONE = "cosori_fryer_cook_done"
