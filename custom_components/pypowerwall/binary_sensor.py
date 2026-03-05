from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PyPowerwallCoordinator
from .data import PyPowerwallConfigEntry
from .entity import PyPowerwallEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PyPowerwallConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entry_id = entry.entry_id
    entities: list[BinarySensorEntity] = [
        PyPowerwallGridConnected(coordinator, entry_id),
        PyPowerwallGridFault(coordinator, entry_id),
        PyPowerwallProxyDegraded(coordinator, entry_id),
        PyPowerwallConnectedToTesla(coordinator, entry_id),
        PyPowerwallAlertsActive(coordinator, entry_id),
    ]

    # Per-pod alert binary sensors
    vitals = coordinator.data.get("vitals") or {}
    battery_blocks = coordinator.data.get("system_status", {}).get("battery_blocks") or []
    block_by_serial: dict[str, dict] = {}
    for block in battery_blocks:
        s = block.get("PackageSerialNumber")
        if s:
            block_by_serial[s] = block

    for vkey, vdata in vitals.items():
        if not vkey.startswith("TEPOD"):
            continue
        parts = vkey.split("--")
        serial = parts[-1] if len(parts) >= 3 else vkey
        part_number = parts[1] if len(parts) >= 2 else ""
        block = block_by_serial.get(serial, {})
        block_type = block.get("Type", "")
        label = "Expansion" if block_type == "BatteryExpansion" else "Primary"
        entities.append(
            PyPowerwallPodAlerts(coordinator, entry_id, vkey, serial, part_number, label)
        )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
#  Main device binary sensors
# ---------------------------------------------------------------------------
class PyPowerwallGridConnected(PyPowerwallEntity, BinarySensorEntity):
    _attr_translation_key = "grid_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_grid_connected"

    @property
    def is_on(self) -> bool | None:
        try:
            return self.coordinator.data["json"].get("grid_status") == 1
        except (KeyError, TypeError):
            return None


class PyPowerwallGridFault(PyPowerwallEntity, BinarySensorEntity):
    _attr_translation_key = "grid_fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_grid_fault"

    @property
    def is_on(self) -> bool | None:
        try:
            faults = self.coordinator.data["system_status"].get("grid_faults", [])
            return len(faults) > 0
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            return {"faults": self.coordinator.data["system_status"].get("grid_faults", [])}
        except (KeyError, TypeError):
            return {}


class PyPowerwallProxyDegraded(PyPowerwallEntity, BinarySensorEntity):
    _attr_translation_key = "proxy_degraded"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_proxy_degraded"

    @property
    def is_on(self) -> bool | None:
        try:
            conn = self.coordinator.data["health"].get("connection_health", {})
            return bool(conn.get("is_degraded", False))
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            conn = self.coordinator.data["health"].get("connection_health", {})
            stats = self.coordinator.data["health"].get("proxy_stats", {})
            return {
                "consecutive_failures": conn.get("consecutive_failures"),
                "total_failures": conn.get("total_failures"),
                "total_successes": conn.get("total_successes"),
                "total_proxy_errors": stats.get("total_errors"),
                "total_proxy_timeouts": stats.get("total_timeouts"),
            }
        except (KeyError, TypeError):
            return {}


class PyPowerwallConnectedToTesla(PyPowerwallEntity, BinarySensorEntity):
    _attr_translation_key = "connected_to_tesla"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_connected_to_tesla"

    @property
    def is_on(self) -> bool | None:
        try:
            return self.coordinator.data["sitemaster"].get("connected_to_tesla")
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            sm = self.coordinator.data["sitemaster"]
            return {
                "status": sm.get("status"),
                "running": sm.get("running"),
            }
        except (KeyError, TypeError):
            return {}


class PyPowerwallAlertsActive(PyPowerwallEntity, BinarySensorEntity):
    """True when any device in vitals reports alerts."""

    _attr_translation_key = "alerts_active"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_alerts_active"

    @property
    def is_on(self) -> bool | None:
        try:
            for val in (self.coordinator.data.get("vitals") or {}).values():
                if isinstance(val, dict) and val.get("alerts"):
                    return True
            return False
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            all_alerts: list[str] = []
            by_device: dict[str, list[str]] = {}
            for key, val in (self.coordinator.data.get("vitals") or {}).items():
                if isinstance(val, dict) and val.get("alerts"):
                    by_device[key] = val["alerts"]
                    all_alerts.extend(val["alerts"])
            return {"total": len(all_alerts), "by_device": by_device}
        except (KeyError, TypeError):
            return {}


# ---------------------------------------------------------------------------
#  Per-pod alert binary sensor (sub-device)
# ---------------------------------------------------------------------------
class PyPowerwallPodAlerts(PyPowerwallEntity, BinarySensorEntity):
    """True when a specific battery pod reports alerts."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Alerts"

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        vitals_key: str,
        serial: str,
        part_number: str,
        device_label: str,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._vitals_key = vitals_key
        self._attr_unique_id = f"{entry_id}_{serial}_pod_alerts"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Powerwall {serial[-4:]} ({device_label})",
            manufacturer="Tesla",
            model=part_number,
            serial_number=serial,
            via_device=(DOMAIN, entry_id),
        )

    @property
    def is_on(self) -> bool | None:
        try:
            alerts = self.coordinator.data["vitals"][self._vitals_key].get("alerts", [])
            return len(alerts) > 0
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            return {"alerts": self.coordinator.data["vitals"][self._vitals_key].get("alerts", [])}
        except (KeyError, TypeError):
            return {}
