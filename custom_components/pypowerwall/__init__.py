from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_CONTROL_SECRET,
    CONF_MAX_BACKUP_MINUTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_MAX_BACKUP_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import PyPowerwallCoordinator
from .data import PyPowerwallConfigEntry, PyPowerwallData
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

def _effective(entry: PyPowerwallConfigEntry, key: str, default):
    """Return the option value if set (even empty), else the data value, else default."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


async def async_setup_entry(hass: HomeAssistant, entry: PyPowerwallConfigEntry) -> bool:
    scan_interval = _effective(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL
    control_secret = _effective(entry, CONF_CONTROL_SECRET, "") or ""
    max_backup_minutes = entry.options.get(CONF_MAX_BACKUP_MINUTES, DEFAULT_MAX_BACKUP_MINUTES)

    coordinator = PyPowerwallCoordinator(
        hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        scan_interval=int(scan_interval),
        control_secret=control_secret,
        max_backup_minutes=int(max_backup_minutes),
        config_entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = PyPowerwallData(coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: PyPowerwallConfigEntry) -> None:
    """Reload when connection-relevant settings change.

    Runtime-only options (max backup minutes, written by the number entity)
    must not bounce the whole integration.
    """
    coordinator = entry.runtime_data.coordinator
    wanted_interval = int(_effective(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL)
    wanted_secret = _effective(entry, CONF_CONTROL_SECRET, "") or ""
    if coordinator.matches(wanted_interval, wanted_secret):
        coordinator.set_max_backup_minutes(
            int(entry.options.get(CONF_MAX_BACKUP_MINUTES, DEFAULT_MAX_BACKUP_MINUTES)), persist=False
        )
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PyPowerwallConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        host, port = entry.data[CONF_HOST], entry.data[CONF_PORT]
        for key in ("proxy_degraded", "proxy_fallback"):
            ir.async_delete_issue(hass, DOMAIN, f"{key}_{host}_{port}")
    return ok
