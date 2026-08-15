"""Services: direct control commands with parameters (for automations)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_ENABLED,
    ATTR_MINUTES,
    ATTR_MODE,
    ATTR_POLICY,
    ATTR_RESERVE,
    DOMAIN,
    SERVICE_CANCEL_MAX_BACKUP,
    SERVICE_SET_GRID_CHARGING,
    SERVICE_SET_GRID_EXPORT,
    SERVICE_SET_MODE,
    SERVICE_SET_RESERVE,
    SERVICE_START_MAX_BACKUP,
)
from .coordinator import PyPowerwallCoordinator

ATTR_CONFIG_ENTRY = "config_entry_id"

OPERATION_MODES = ["self_consumption", "backup", "autonomous"]
GRID_EXPORT_MODES = ["battery_ok", "pv_only", "never"]

_ENTRY = {vol.Optional(ATTR_CONFIG_ENTRY): cv.string}

SCHEMA_SET_RESERVE = vol.Schema({**_ENTRY, vol.Required(ATTR_RESERVE): vol.All(vol.Coerce(int), vol.Range(0, 100))})
SCHEMA_SET_MODE = vol.Schema({**_ENTRY, vol.Required(ATTR_MODE): vol.In(OPERATION_MODES)})
SCHEMA_SET_GRID_EXPORT = vol.Schema({**_ENTRY, vol.Required(ATTR_POLICY): vol.In(GRID_EXPORT_MODES)})
SCHEMA_SET_GRID_CHARGING = vol.Schema({**_ENTRY, vol.Required(ATTR_ENABLED): cv.boolean})
SCHEMA_START_MAX_BACKUP = vol.Schema(
    {**_ENTRY, vol.Optional(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(1, 480))}
)
SCHEMA_CANCEL_MAX_BACKUP = vol.Schema(_ENTRY)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> PyPowerwallCoordinator:
    """Resolve the target coordinator: explicit config_entry_id, or the only loaded entry."""
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if hasattr(e, "runtime_data")]
    entry_id = call.data.get(ATTR_CONFIG_ENTRY)
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
    elif len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_ambiguous")
    coordinator = entries[0].runtime_data.coordinator
    if not coordinator.has_control_secret:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_control_secret")
    return coordinator


async def _send(coordinator: PyPowerwallCoordinator, path: str, value, what: str) -> None:
    if not await coordinator.send_command(path, value):
        raise HomeAssistantError(f"pypowerwall proxy rejected {what}")
    await coordinator.async_request_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_RESERVE):
        return

    async def set_reserve(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _send(c, "/control/reserve", int(call.data[ATTR_RESERVE]), f"reserve {call.data[ATTR_RESERVE]}%")

    async def set_mode(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _send(c, "/control/mode", call.data[ATTR_MODE], f"mode {call.data[ATTR_MODE]}")

    async def set_grid_export(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _send(c, "/control/grid_export", call.data[ATTR_POLICY], f"grid export {call.data[ATTR_POLICY]}")

    async def set_grid_charging(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        val = "true" if call.data[ATTR_ENABLED] else "false"
        await _send(c, "/control/grid_charging", val, f"grid charging {val}")

    async def start_max_backup(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        minutes = int(call.data.get(ATTR_MINUTES, c.max_backup_minutes))
        # A lingering (expired) event must be cancelled before scheduling a new one
        data = (c.data or {}).get("control_max_backup")
        if isinstance(data, dict) and data.get("manual_backup") is not None:
            await _send(c, "/control/max_backup", "cancel", "max backup cancel")
        await _send(c, "/control/max_backup", minutes * 60, f"max backup for {minutes} min")

    async def cancel_max_backup(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _send(c, "/control/max_backup", "cancel", "max backup cancel")

    hass.services.async_register(DOMAIN, SERVICE_SET_RESERVE, set_reserve, schema=SCHEMA_SET_RESERVE)
    hass.services.async_register(DOMAIN, SERVICE_SET_MODE, set_mode, schema=SCHEMA_SET_MODE)
    hass.services.async_register(DOMAIN, SERVICE_SET_GRID_EXPORT, set_grid_export, schema=SCHEMA_SET_GRID_EXPORT)
    hass.services.async_register(DOMAIN, SERVICE_SET_GRID_CHARGING, set_grid_charging, schema=SCHEMA_SET_GRID_CHARGING)
    hass.services.async_register(DOMAIN, SERVICE_START_MAX_BACKUP, start_max_backup, schema=SCHEMA_START_MAX_BACKUP)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_MAX_BACKUP, cancel_max_backup, schema=SCHEMA_CANCEL_MAX_BACKUP)
