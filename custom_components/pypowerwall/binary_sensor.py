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
from .entity import (
    PyPowerwallEntity,
    build_block_by_serial,
    build_device_labels,
    parse_pod_data,
    parse_vitals_key,
)


# ---------------------------------------------------------------------------
#  Pod health flag definitions
# ---------------------------------------------------------------------------
POD_HEALTH_FLAGS: tuple[tuple[str, str, BinarySensorDeviceClass | None, bool], ...] = (
    ("pod_permanently_faulted", "POD_PermanentlyFaulted", BinarySensorDeviceClass.PROBLEM, True),
    ("pod_persistently_faulted", "POD_PersistentlyFaulted", BinarySensorDeviceClass.PROBLEM, True),
    ("pod_active_heating", "POD_ActiveHeating", None, False),
    ("pod_charge_complete", "POD_ChargeComplete", None, False),
    ("pod_charge_request", "POD_ChargeRequest", None, False),
    ("pod_discharge_complete", "POD_DischargeComplete", None, False),
    ("pod_backup_ready", "backup_ready", None, False),
    ("pod_wobble_detected", "wobble_detected", BinarySensorDeviceClass.PROBLEM, False),
    ("pod_charge_power_clamped", "charge_power_clamped", BinarySensorDeviceClass.PROBLEM, False),
)


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

    vitals = coordinator.data.get("vitals") or {}
    block_by_serial = build_block_by_serial(coordinator.data)
    device_labels = build_device_labels(block_by_serial)

    # --- Per-pod alert and health binary sensors ---
    for vkey, vdata in vitals.items():
        if not vkey.startswith("TEPOD"):
            continue
        part_number, serial = parse_vitals_key(vkey)
        label = device_labels.get(serial, "Primary")
        entities.append(
            PyPowerwallPodAlerts(
                coordinator, entry_id, vkey, serial, part_number, label
            )
        )
        # Pod health flags (from /pod)
        for trans_key, flag_field, dev_class, enabled in POD_HEALTH_FLAGS:
            entities.append(
                PyPowerwallPodHealthFlag(
                    coordinator,
                    entry_id,
                    serial,
                    part_number,
                    label,
                    flag_field,
                    trans_key,
                    dev_class,
                    enabled,
                )
            )

    # --- Island grid connected (TESYNC) ---
    for vkey, vdata in vitals.items():
        if not vkey.startswith("TESYNC"):
            continue
        part_number, serial = parse_vitals_key(vkey)
        entities.append(
            PyPowerwallIslandGridConnected(
                coordinator, entry_id, vkey, serial, part_number
            )
        )

    # --- PV string connected binary sensors (multi-PVAC) ---
    pvac_entries: list[tuple[str, str, str]] = []  # (vkey, part, serial)
    pvs_by_serial: dict[str, str] = {}  # serial → vkey

    for vkey in vitals:
        if vkey.startswith("PVAC"):
            part, serial = parse_vitals_key(vkey)
            pvac_entries.append((vkey, part, serial))
        elif vkey.startswith("PVS"):
            _, pvs_serial = parse_vitals_key(vkey)
            pvs_by_serial[pvs_serial] = vkey

    for pvac_vkey, pvac_part, pvac_serial in pvac_entries:
        label = device_labels.get(pvac_serial, "Primary")
        pvs_key = pvs_by_serial.get(pvac_serial)
        if pvs_key:
            for string_id in ("A", "B", "C", "D", "E", "F"):
                entities.append(
                    PyPowerwallStringConnected(
                        coordinator,
                        entry_id,
                        pvs_key,
                        pvac_serial,
                        pvac_part,
                        string_id,
                        label=label,
                    )
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
            return {
                "faults": self.coordinator.data["system_status"].get(
                    "grid_faults", []
                )
            }
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

    _attr_translation_key = "pod_alerts"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

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
            alerts = self.coordinator.data["vitals"][self._vitals_key].get(
                "alerts", []
            )
            return len(alerts) > 0
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            return {
                "alerts": self.coordinator.data["vitals"][self._vitals_key].get(
                    "alerts", []
                )
            }
        except (KeyError, TypeError):
            return {}


# ---------------------------------------------------------------------------
#  Pod health flag binary sensor (from /pod data)
# ---------------------------------------------------------------------------
class PyPowerwallPodHealthFlag(PyPowerwallEntity, BinarySensorEntity):
    """Binary sensor for a pod health flag from the /pod endpoint."""

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        serial: str,
        part_number: str,
        device_label: str,
        flag_key: str,
        translation_key: str,
        device_class: BinarySensorDeviceClass | None,
        enabled_default: bool,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._serial = serial
        self._flag_key = flag_key
        self._attr_unique_id = f"{entry_id}_{serial}_pod_health_{translation_key}"
        self._attr_translation_key = translation_key
        if device_class:
            self._attr_device_class = device_class
        self._attr_entity_registry_enabled_default = enabled_default
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
            pod_by_serial = parse_pod_data(self.coordinator.data.get("pod"))
            pw_data = pod_by_serial.get(self._serial, {})
            val = pw_data.get(self._flag_key)
            if val is None:
                return None
            return bool(val)
        except (KeyError, TypeError):
            return None


# ---------------------------------------------------------------------------
#  Island grid connected binary sensor (TESYNC)
# ---------------------------------------------------------------------------
class PyPowerwallIslandGridConnected(PyPowerwallEntity, BinarySensorEntity):
    """True when ISLAND_GridConnected contains 'Connected'."""

    _attr_translation_key = "island_grid_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        vitals_key: str,
        serial: str,
        part_number: str,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._vitals_key = vitals_key
        self._attr_unique_id = f"{entry_id}_{serial}_island_grid_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name="Sync Controller",
            manufacturer="Tesla",
            model=part_number or None,
            via_device=(DOMAIN, entry_id),
        )

    @property
    def is_on(self) -> bool | None:
        try:
            val = self.coordinator.data["vitals"][self._vitals_key].get(
                "ISLAND_GridConnected"
            )
            if val is None:
                return None
            return "Connected" in str(val)
        except (KeyError, TypeError):
            return None


# ---------------------------------------------------------------------------
#  PV string connected binary sensor (PVS)
# ---------------------------------------------------------------------------
class PyPowerwallStringConnected(PyPowerwallEntity, BinarySensorEntity):
    """PV string connected status from PVS device."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        pvs_key: str,
        pvac_serial: str,
        pvac_part: str,
        string_id: str,
        label: str = "Primary",
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._pvs_key = pvs_key
        self._string_id = string_id
        self._attr_unique_id = (
            f"{entry_id}_{pvac_serial}_string_{string_id}_connected"
        )
        self._attr_name = f"String {string_id} Connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pvac_serial)},
            name=f"Powerwall {pvac_serial[-4:]} ({label})",
            manufacturer="Tesla",
            model=pvac_part,
            serial_number=pvac_serial,
            via_device=(DOMAIN, entry_id),
        )

    @property
    def is_on(self) -> bool | None:
        try:
            pvs_data = self.coordinator.data["vitals"][self._pvs_key]
            val = pvs_data.get(f"PVS_String{self._string_id}_Connected")
            if val is None:
                return None
            return bool(val)
        except (KeyError, TypeError):
            return None
