from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .coordinator import PyPowerwallCoordinator
from .data import PyPowerwallConfigEntry
from .entity import PyPowerwallEntity, build_block_by_serial, build_device_labels, parse_vitals_key
from .sensor_descriptions import (
    ENERGY_SENSOR_KEYS,
    GRID_METER_SENSORS,
    INVERTER_SENSORS,
    ISLAND_SENSORS,
    MAIN_SENSORS,
    POD_SENSORS,
    PVAC_OUTPUT_SENSORS,
    STRING_FIELDS,
    PyPowerwallSensorDescription,
    VitalsSensorDescription,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Entity classes
# ---------------------------------------------------------------------------
class PyPowerwallSensor(PyPowerwallEntity, SensorEntity):
    """Static sensor on the main PyPowerwall device."""

    entity_description: PyPowerwallSensorDescription

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        description: PyPowerwallSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, TypeError, ZeroDivisionError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "alert_count":
            try:
                breakdown: dict[str, list[str]] = {}
                for key, val in (self.coordinator.data.get("vitals") or {}).items():
                    if isinstance(val, dict) and val.get("alerts"):
                        breakdown[key] = val["alerts"]
                return {"alerts_by_device": breakdown}
            except (KeyError, TypeError):
                return None
        if self.entity_description.key == "troubleshooting_problems":
            try:
                problems = self.coordinator.data.get("troubleshooting") or []
                return {"problems": problems}
            except (KeyError, TypeError):
                return None
        return None


class PyPowerwallVitalsSensor(PyPowerwallEntity, SensorEntity):
    """Sensor for a vitals device (pod, inverter, meter, etc.) — shown as a sub-device."""

    entity_description: VitalsSensorDescription

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        vitals_key: str,
        serial: str,
        part_number: str,
        device_label: str,
        description: VitalsSensorDescription,
        device_name: str | None = None,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._vitals_key = vitals_key
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{serial}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=device_name or f"Powerwall {serial[-4:]} ({device_label})",
            manufacturer="Tesla",
            model=part_number or None,
            serial_number=serial if serial != "tesync" else None,
            via_device=(DOMAIN, entry_id),
        )

    @property
    def native_value(self) -> StateType:
        try:
            device_data = self.coordinator.data["vitals"][self._vitals_key]
            return self.entity_description.value_fn(device_data)
        except (KeyError, TypeError, ZeroDivisionError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "pod_soc":
            return None
        try:
            device_data = self.coordinator.data["vitals"][self._vitals_key]
            return {"alerts": device_data.get("alerts", [])}
        except (KeyError, TypeError):
            return None


class PyPowerwallStringSensor(PyPowerwallEntity, SensorEntity):
    """PV string sensor (A–F) under a Powerwall device."""

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        pvac_key: str,
        pvac_serial: str,
        pvac_part: str,
        string_id: str,
        field_key: str,
        field_label: str,
        unit: str,
        device_class: SensorDeviceClass,
        icon: str | None,
        display_precision: int,
        enabled_default: bool = True,
        label: str = "Primary",
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._pvac_key = pvac_key
        self._string_id = string_id
        self._field_key = field_key
        self._attr_unique_id = (
            f"{entry_id}_{pvac_serial}_string_{string_id}_{field_key}"
        )
        self._attr_name = f"String {string_id} {field_label}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = display_precision
        self._attr_entity_registry_enabled_default = enabled_default
        if icon:
            self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pvac_serial)},
            name=f"Powerwall {pvac_serial[-4:]} ({label})",
            manufacturer="Tesla",
            model=pvac_part,
            serial_number=pvac_serial,
            via_device=(DOMAIN, entry_id),
        )

    @property
    def native_value(self) -> StateType:
        try:
            device_data = self.coordinator.data["vitals"][self._pvac_key]
            pv_key = f"PVAC_PVMeasured{self._field_key.capitalize()}_{self._string_id}"
            if self._field_key == "current":
                pv_key = f"PVAC_PVCurrent_{self._string_id}"
            return device_data.get(pv_key)
        except (KeyError, TypeError):
            return None


class PyPowerwallStringStateSensor(PyPowerwallEntity, SensorEntity):
    """PV string state sensor (A–F) — text value from PVAC_PvState_X."""

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        pvac_key: str,
        pvac_serial: str,
        pvac_part: str,
        string_id: str,
        label: str = "Primary",
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._pvac_key = pvac_key
        self._string_id = string_id
        self._attr_unique_id = (
            f"{entry_id}_{pvac_serial}_string_{string_id}_state"
        )
        self._attr_name = f"String {string_id} State"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pvac_serial)},
            name=f"Powerwall {pvac_serial[-4:]} ({label})",
            manufacturer="Tesla",
            model=pvac_part,
            serial_number=pvac_serial,
            via_device=(DOMAIN, entry_id),
        )

    @property
    def native_value(self) -> StateType:
        try:
            device_data = self.coordinator.data["vitals"][self._pvac_key]
            return device_data.get(f"PVAC_PvState_{self._string_id}")
        except (KeyError, TypeError):
            return None


# ---------------------------------------------------------------------------
#  Platform setup
# ---------------------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: PyPowerwallConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entry_id = entry.entry_id
    entities: list[SensorEntity] = []

    # --- Main device sensors ---
    for desc in MAIN_SENSORS:
        if desc.key in ENERGY_SENSOR_KEYS:
            try:
                has_data = desc.value_fn(coordinator.data) is not None
            except (KeyError, TypeError, AttributeError):
                has_data = False
            if not has_data:
                _LOGGER.debug("Skipping %s: proxy reports no lifetime counter for it", desc.key)
                continue
        entities.append(PyPowerwallSensor(coordinator, entry_id, desc))

    vitals = coordinator.data.get("vitals") or {}
    block_by_serial = build_block_by_serial(coordinator.data)
    device_labels = build_device_labels(block_by_serial, vitals)

    # --- Battery pod sensors (TEPOD) ---
    for vkey, _vdata in vitals.items():
        if not vkey.startswith("TEPOD"):
            continue
        part_number, serial = parse_vitals_key(vkey)
        label = device_labels.get(serial, "Primary")
        for desc in POD_SENSORS:
            entities.append(
                PyPowerwallVitalsSensor(
                    coordinator, entry_id, vkey, serial, part_number, label, desc
                )
            )

    # --- Inverter sensors (TEPINV) — same device as matching pod ---
    for vkey, _vdata in vitals.items():
        if not vkey.startswith("TEPINV"):
            continue
        part_number, serial = parse_vitals_key(vkey)
        label = device_labels.get(serial, "Primary")
        for desc in INVERTER_SENSORS:
            entities.append(
                PyPowerwallVitalsSensor(
                    coordinator, entry_id, vkey, serial, part_number, label, desc
                )
            )

    # --- Collect ALL PVACs and PVS devices ---
    pvac_entries: list[tuple[str, str, str]] = []  # (vkey, part, serial)
    pvs_by_serial: dict[str, str] = {}  # serial → vkey

    for vkey in vitals:
        if vkey.startswith("PVAC"):
            part, serial = parse_vitals_key(vkey)
            pvac_entries.append((vkey, part, serial))
        elif vkey.startswith("PVS"):
            _, pvs_serial = parse_vitals_key(vkey)
            pvs_by_serial[pvs_serial] = vkey

    # --- PVAC output sensors + PV string sensors for ALL PVACs ---
    for pvac_vkey, pvac_part, pvac_serial in pvac_entries:
        label = device_labels.get(pvac_serial, "Primary")

        # PVAC output sensors for every PVAC
        for desc in PVAC_OUTPUT_SENSORS:
            entities.append(
                PyPowerwallVitalsSensor(
                    coordinator,
                    entry_id,
                    pvac_vkey,
                    pvac_serial,
                    pvac_part,
                    label,
                    desc,
                )
            )

        # PV string sensors only if matching PVS found by serial
        pvs_key = pvs_by_serial.get(pvac_serial)
        if pvs_key:
            # Determine which strings are connected
            connected_strings: set[str] = set()
            pvs_data = vitals.get(pvs_key, {})
            for string_id in ("A", "B", "C", "D", "E", "F"):
                if pvs_data.get(f"PVS_String{string_id}_Connected"):
                    connected_strings.add(string_id)

            for string_id in ("A", "B", "C", "D", "E", "F"):
                enabled = string_id in connected_strings if connected_strings else True
                for field_key, field_label, unit, dc, icon, precision in STRING_FIELDS:
                    entities.append(
                        PyPowerwallStringSensor(
                            coordinator,
                            entry_id,
                            pvac_vkey,
                            pvac_serial,
                            pvac_part,
                            string_id,
                            field_key,
                            field_label,
                            unit,
                            dc,
                            icon,
                            precision,
                            enabled_default=enabled,
                            label=label,
                        )
                    )
                # String state sensor
                entities.append(
                    PyPowerwallStringStateSensor(
                        coordinator,
                        entry_id,
                        pvac_vkey,
                        pvac_serial,
                        pvac_part,
                        string_id,
                        label=label,
                    )
                )

    # --- Grid meter sensors (TEMSA) ---
    for vkey, _vdata in vitals.items():
        if not vkey.startswith("TEMSA"):
            continue
        part_number, serial = parse_vitals_key(vkey)
        for desc in GRID_METER_SENSORS:
            entities.append(
                PyPowerwallVitalsSensor(
                    coordinator,
                    entry_id,
                    vkey,
                    serial,
                    part_number,
                    "Grid Meter",
                    desc,
                    device_name=f"Grid Meter {serial[-3:]}"
                    if len(serial) >= 3
                    else "Grid Meter",
                )
            )

    # --- Island controller sensors (TESYNC) ---
    for vkey, _vdata in vitals.items():
        if not vkey.startswith("TESYNC"):
            continue
        part_number, serial = parse_vitals_key(vkey)
        for desc in ISLAND_SENSORS:
            entities.append(
                PyPowerwallVitalsSensor(
                    coordinator,
                    entry_id,
                    vkey,
                    serial,
                    part_number,
                    "Sync",
                    desc,
                    device_name="Sync Controller",
                )
            )

    _LOGGER.info("Setting up %d PyPowerwall sensor entities", len(entities))
    async_add_entities(entities)
