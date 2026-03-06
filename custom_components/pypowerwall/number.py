from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
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
        async_add_entities([PyPowerwallBackupReserve(coordinator, entry.entry_id)])


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
        # Prefer /json reserve, fall back to /api/operation
        val = d.get("json", {}).get("reserve")
        if val is not None:
            return float(val)
        op = d.get("operation")
        if op and isinstance(op, dict):
            val = op.get("backup_reserve_percent")
            if val is not None:
                return float(val)
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
