"""Constants for the COSORI CS358-AF integration."""

from datetime import timedelta

DOMAIN = "cosori_fryer"

CONF_TERMINAL_ID = "terminal_id"
CONF_TOKEN = "token"
CONF_ACCOUNT_ID = "account_id"
CONF_COUNTRY_CODE = "country_code"
CONF_REGION = "region"

POLL_ACTIVE = timedelta(seconds=30)
POLL_IDLE = timedelta(minutes=1)
# Keep polling fast for a while after activity, so changes made on the fryer show up.
ACTIVE_LINGER_SECONDS = 600
# VeSync rate-limits logins, and a new login signs other clients out.
RELOGIN_COOLDOWN_SECONDS = 300

DEFAULT_TEMPERATURE = 360
DEFAULT_MINUTES = 10

EVENT_PREHEAT_DONE = "cosori_fryer_preheat_done"
EVENT_COOK_DONE = "cosori_fryer_cook_done"

# Fryer status codes and the state names shown for them.
STATUS = {
    "standby": "idle",
    "cookEnd": "finished",
    "pullOut": "basket_out",
    "ready": "ready",
    "heating": "preheating",
    "preheatStop": "preheat_paused",
    "preheatEnd": "preheated",
    "cooking": "cooking",
    "cookStop": "paused",
    "keeping": "keeping_warm",
}
