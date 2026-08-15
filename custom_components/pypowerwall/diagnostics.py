"""Diagnostics support for PyPowerwall (Settings -> Devices -> ... -> Download diagnostics)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CONTROL_SECRET
from .data import PyPowerwallConfigEntry

# Config keys and payload keys that must never leave the system.
TO_REDACT = {
    CONF_CONTROL_SECRET,
    "token",
    "site_name",
    "email",
    "password",
    "host",  # proxy transport hosts inside /health and the entry itself
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PyPowerwallConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_s": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            "has_control_secret": coordinator.has_control_secret,
        },
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
    }
