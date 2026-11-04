"""Constants for the Cosori air fryer integration."""

from datetime import timedelta

DOMAIN = "cosori_fryer"
POLL_ACTIVE = timedelta(seconds=15)
POLL_IDLE = timedelta(seconds=60)
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
    "keeping_warm": "keeping_warm",
}
