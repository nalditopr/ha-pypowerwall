from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PyPowerwallCoordinator
from .data import PyPowerwallConfigEntry
from .entity import PyPowerwallEntity

_LOGGER = logging.getLogger(__name__)

OPERATION_MODES = ["self_consumption", "backup", "autonomous"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PyPowerwallConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    if coordinator.has_control_secret:
        async_add_entities([PyPowerwallOperationMode(coordinator, entry.entry_id)])


class PyPowerwallOperationMode(PyPowerwallEntity, SelectEntity):
    """Operation mode control."""

    _attr_options = OPERATION_MODES
    _attr_translation_key = "operation_mode_select"
    _attr_icon = "mdi:cog"

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_operation_mode_select"

    @property
    def current_option(self) -> str | None:
        op = self.coordinator.data.get("operation")
        if op and isinstance(op, dict):
            mode = op.get("real_mode")
            if mode in OPERATION_MODES:
                return mode
        return None

    async def async_select_option(self, option: str) -> None:
        success = await self.coordinator.send_command(
            "/control/mode", option
        )
        if not success:
            raise HomeAssistantError(
                f"Failed to set operation mode to {option}"
            )
        await self.coordinator.async_request_refresh()
