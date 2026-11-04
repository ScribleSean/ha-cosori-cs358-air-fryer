"""Redacted config entry diagnostics."""

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data

TO_REDACT = {
    "email",
    "password",
    "token",
    "account_id",
    "cid",
    "uuid",
    "mac",
    "serial",
    "device_name",
    "terminal_id",
}


async def async_get_config_entry_diagnostics(hass, entry):
    """Whitelist device fields and redact credentials recursively."""
    device = entry.runtime_data.device
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "device": {
                "cid": device.cid,
                "model": device.device_type,
                "device_name": device.device_name,
            },
            "state": asdict(entry.runtime_data.data),
        },
        TO_REDACT,
    )
