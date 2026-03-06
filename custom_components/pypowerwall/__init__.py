from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_CONTROL_SECRET, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .coordinator import PyPowerwallCoordinator
from .data import PyPowerwallConfigEntry, PyPowerwallData

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: PyPowerwallConfigEntry) -> bool:
    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    control_secret = entry.data.get(CONF_CONTROL_SECRET, "")
    # Options can override data
    if entry.options.get(CONF_SCAN_INTERVAL):
        scan_interval = entry.options[CONF_SCAN_INTERVAL]
    if entry.options.get(CONF_CONTROL_SECRET) is not None:
        control_secret = entry.options[CONF_CONTROL_SECRET]

    coordinator = PyPowerwallCoordinator(
        hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        scan_interval=scan_interval,
        control_secret=control_secret,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = PyPowerwallData(coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Update interval on options change
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: PyPowerwallConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PyPowerwallConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
