from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PyPowerwallCoordinator
from .data import PyPowerwallConfigEntry
from .entity import PyPowerwallEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PyPowerwallConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    if coordinator.has_control_secret:
        async_add_entities([
            PyPowerwallBackupReserve(coordinator, entry.entry_id),
            PyPowerwallMaxBackupDuration(coordinator, entry.entry_id),
        ])


class PyPowerwallBackupReserve(PyPowerwallEntity, NumberEntity):
    """Backup reserve percentage control."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-lock"
    _attr_translation_key = "backup_reserve"

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_backup_reserve"

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        # Prefer /api/operation (actual setting), fall back to /json reserve
        op = d.get("operation")
        if op and isinstance(op, dict):
            val = op.get("backup_reserve_percent")
            if val is not None:
                return max(0.0, min(100.0, float(val)))
        val = d.get("json", {}).get("reserve")
        if val is not None:
            return max(0.0, min(100.0, float(val)))
        return None

    async def async_set_native_value(self, value: float) -> None:
        success = await self.coordinator.send_command(
            "/control/reserve", int(value)
        )
        if not success:
            raise HomeAssistantError(
                f"Failed to set backup reserve to {value}%"
            )
        await self.coordinator.async_request_refresh()


class PyPowerwallMaxBackupDuration(PyPowerwallEntity, NumberEntity):
    """Max backup duration control."""

    _attr_native_min_value = 1
    _attr_native_max_value = 480
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:timer-outline"
    _attr_translation_key = "max_backup_duration"

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_max_backup_duration"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.max_backup_minutes

    async def async_set_native_value(self, value: float) -> None:
        # Persisted in the config entry options so it survives reloads/restarts.
        self.coordinator.set_max_backup_minutes(int(value))
        self.async_write_ha_state()
