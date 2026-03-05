from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .coordinator import PyPowerwallCoordinator
from .data import PyPowerwallConfigEntry
from .entity import PyPowerwallEntity

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Description types
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class PyPowerwallSensorDescription(SensorEntityDescription):
    """Static sensor — value_fn receives full coordinator data dict."""

    value_fn: Any


@dataclass(frozen=True, kw_only=True)
class VitalsSensorDescription(SensorEntityDescription):
    """Vitals device sensor — value_fn receives the single device dict."""

    value_fn: Any


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def _vitals_frequency(d: dict) -> float | None:
    for key, val in (d.get("vitals") or {}).items():
        if key.startswith("PVAC"):
            return val.get("PVAC_Fout")
    return None


def _parse_vitals_key(key: str) -> tuple[str, str]:
    """Return (part_number, serial) from a vitals key like TEPOD--1707000-21-K--TG12..."""
    parts = key.split("--")
    serial = parts[-1] if len(parts) >= 3 else key
    part_number = parts[1] if len(parts) >= 2 else ""
    return part_number, serial


# ---------------------------------------------------------------------------
#  Main device sensors
# ---------------------------------------------------------------------------
MAIN_SENSORS: tuple[PyPowerwallSensorDescription, ...] = (
    # Power
    PyPowerwallSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        value_fn=lambda d: d["json"].get("solar"),
    ),
    PyPowerwallSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
        value_fn=lambda d: d["json"].get("battery"),
    ),
    PyPowerwallSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        value_fn=lambda d: d["json"].get("grid"),
    ),
    PyPowerwallSensorDescription(
        key="home_power",
        translation_key="home_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
        value_fn=lambda d: d["json"].get("home"),
    ),
    # Battery
    PyPowerwallSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: round(d["json"]["soe"], 1) if d["json"].get("soe") is not None else None,
    ),
    PyPowerwallSensorDescription(
        key="battery_reserve",
        translation_key="battery_reserve",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-lock",
        value_fn=lambda d: d["json"].get("reserve"),
    ),
    PyPowerwallSensorDescription(
        key="time_remaining",
        translation_key="time_remaining",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        value_fn=lambda d: round(d["json"]["time_remaining_hours"], 2) if d["json"].get("time_remaining_hours") is not None else None,
    ),
    # Grid
    PyPowerwallSensorDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["aggregates"].get("site", {}).get("instant_average_voltage"),
    ),
    PyPowerwallSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_vitals_frequency,
    ),
    # Alerts
    PyPowerwallSensorDescription(
        key="alert_count",
        translation_key="alert_count",
        icon="mdi:alert-circle-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(
            len(v.get("alerts", []))
            for v in (d.get("vitals") or {}).values()
            if isinstance(v, dict)
        ),
    ),
    # Diagnostics
    PyPowerwallSensorDescription(
        key="operation_mode",
        translation_key="operation_mode",
        icon="mdi:cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("operation") or {}).get("real_mode"),
    ),
    PyPowerwallSensorDescription(
        key="pypowerwall_version",
        translation_key="pypowerwall_version",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("health") or {}).get("pypowerwall"),
    ),
    PyPowerwallSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("version_info") or {}).get("version"),
    ),
)


# ---------------------------------------------------------------------------
#  Battery pod sensors (per TEPOD device)
# ---------------------------------------------------------------------------
POD_SENSORS: tuple[VitalsSensorDescription, ...] = (
    VitalsSensorDescription(
        key="pod_soc",
        name="SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: round(
            d.get("POD_nom_energy_remaining", 0) / max(d.get("POD_nom_full_pack_energy", 1), 1) * 100, 1
        ),
    ),
    VitalsSensorDescription(
        key="pod_energy_remaining",
        name="Energy Remaining",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-outline",
        value_fn=lambda d: d.get("POD_nom_energy_remaining"),
    ),
    VitalsSensorDescription(
        key="pod_energy_to_charge",
        name="Energy to Charge",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging-outline",
        value_fn=lambda d: d.get("POD_nom_energy_to_be_charged"),
    ),
    VitalsSensorDescription(
        key="pod_full_energy",
        name="Full Pack Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("POD_nom_full_pack_energy"),
    ),
)


# ---------------------------------------------------------------------------
#  Inverter sensors (per TEPINV device)
# ---------------------------------------------------------------------------
INVERTER_SENSORS: tuple[VitalsSensorDescription, ...] = (
    VitalsSensorDescription(
        key="inverter_power",
        name="Inverter Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("PINV_Pout"),
    ),
    VitalsSensorDescription(
        key="inverter_voltage",
        name="Inverter Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("PINV_Vout"),
    ),
    VitalsSensorDescription(
        key="inverter_frequency",
        name="Inverter Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("PINV_Fout"),
    ),
    VitalsSensorDescription(
        key="inverter_state",
        name="Inverter State",
        icon="mdi:state-machine",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("PINV_State"),
    ),
)


# ---------------------------------------------------------------------------
#  PV string sensors (per PVAC string A–F)
# ---------------------------------------------------------------------------
STRING_FIELDS = (
    ("power", "Power", UnitOfPower.WATT, SensorDeviceClass.POWER, "mdi:solar-power"),
    ("voltage", "Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, None),
    ("current", "Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, None),
)


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
        if self.entity_description.key != "alert_count":
            return None
        # Breakdown alerts per device
        try:
            breakdown: dict[str, list[str]] = {}
            for key, val in (self.coordinator.data.get("vitals") or {}).items():
                if isinstance(val, dict) and val.get("alerts"):
                    breakdown[key] = val["alerts"]
            return {"alerts_by_device": breakdown}
        except (KeyError, TypeError):
            return None


class PyPowerwallVitalsSensor(PyPowerwallEntity, SensorEntity):
    """Sensor for a vitals device (pod or inverter) — shown as a sub-device."""

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
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._vitals_key = vitals_key
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{serial}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Powerwall {serial[-4:]} ({device_label})",
            manufacturer="Tesla",
            model=part_number,
            serial_number=serial,
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
    """PV string sensor (A–F) under the primary Powerwall device."""

    def __init__(
        self,
        coordinator: PyPowerwallCoordinator,
        entry_id: str,
        pvac_serial: str,
        pvac_part: str,
        string_id: str,
        field_key: str,
        field_label: str,
        unit: str,
        device_class: SensorDeviceClass,
        icon: str | None,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._string_id = string_id
        self._field_key = field_key
        self._attr_unique_id = f"{entry_id}_{pvac_serial}_string_{string_id}_{field_key}"
        self._attr_name = f"String {string_id} {field_label}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        if icon:
            self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pvac_serial)},
            name=f"Powerwall {pvac_serial[-4:]} (Primary)",
            manufacturer="Tesla",
            model=pvac_part,
            serial_number=pvac_serial,
            via_device=(DOMAIN, entry_id),
        )

    @property
    def native_value(self) -> StateType:
        try:
            vitals = self.coordinator.data["vitals"]
            # find PVAC key containing our serial
            for key, val in vitals.items():
                if key.startswith("PVAC"):
                    pv_key = f"PVAC_PVMeasured{self._field_key}_{self._string_id}"
                    if self._field_key == "Current":
                        pv_key = f"PVAC_PVCurrent_{self._string_id}"
                    result = val.get(pv_key)
                    if result is not None:
                        return result
            return None
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
        entities.append(PyPowerwallSensor(coordinator, entry_id, desc))

    vitals = coordinator.data.get("vitals") or {}
    battery_blocks = (
        coordinator.data.get("system_status", {}).get("battery_blocks") or []
    )

    # Build serial → battery block lookup
    block_by_serial: dict[str, dict] = {}
    for block in battery_blocks:
        s = block.get("PackageSerialNumber")
        if s:
            block_by_serial[s] = block

    # --- Battery pod sensors (TEPOD) ---
    for vkey, vdata in vitals.items():
        if not vkey.startswith("TEPOD"):
            continue
        part_number, serial = _parse_vitals_key(vkey)
        block = block_by_serial.get(serial, {})
        block_type = block.get("Type", "")
        label = "Expansion" if block_type == "BatteryExpansion" else "Primary"
        for desc in POD_SENSORS:
            entities.append(
                PyPowerwallVitalsSensor(
                    coordinator, entry_id, vkey, serial, part_number, label, desc
                )
            )

    # --- Inverter sensors (TEPINV) — same device as matching pod ---
    for vkey, vdata in vitals.items():
        if not vkey.startswith("TEPINV"):
            continue
        part_number, serial = _parse_vitals_key(vkey)
        block = block_by_serial.get(serial, {})
        block_type = block.get("Type", "")
        label = "Expansion" if block_type == "BatteryExpansion" else "Primary"
        for desc in INVERTER_SENSORS:
            entities.append(
                PyPowerwallVitalsSensor(
                    coordinator, entry_id, vkey, serial, part_number, label, desc
                )
            )

    # --- PV string sensors (from PVAC) ---
    for vkey, vdata in vitals.items():
        if not vkey.startswith("PVAC"):
            continue
        pvac_part, pvac_serial = _parse_vitals_key(vkey)
        for string_id in ("A", "B", "C", "D", "E", "F"):
            for field_key, field_label, unit, dc, icon in STRING_FIELDS:
                entities.append(
                    PyPowerwallStringSensor(
                        coordinator,
                        entry_id,
                        pvac_serial,
                        pvac_part,
                        string_id,
                        field_key,
                        field_label,
                        unit,
                        dc,
                        icon,
                    )
                )

    _LOGGER.info("Setting up %d PyPowerwall sensor entities", len(entities))
    async_add_entities(entities)
