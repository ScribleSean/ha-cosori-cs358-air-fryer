"""Constants for the COSORI CS358-AF integration."""

from datetime import timedelta

DOMAIN = "cosori_fryer"

CONF_TERMINAL_ID = "terminal_id"
CONF_TOKEN = "token"  # noqa: S105 - a config key, not a secret
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

# Fryer status codes and the state keys used for them. The first eight match
# Home Assistant's built-in VeSync integration.
STATUS = {
    "standby": "standby",
    "heating": "heating",
    "preheatStop": "preheat_stop",
    "preheatEnd": "preheat_end",
    "cooking": "cooking",
    "cookStop": "cooking_stop",
    "cookEnd": "cooking_end",
    "pullOut": "pull_out",
    "ready": "ready",
    "keeping": "keeping_warm",
}
