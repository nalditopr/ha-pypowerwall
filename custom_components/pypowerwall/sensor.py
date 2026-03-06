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
from .entity import PyPowerwallEntity, build_block_by_serial, build_device_labels, parse_vitals_key

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
        suggested_display_precision=0,
        icon="mdi:solar-power",
        value_fn=lambda d: d["json"].get("solar"),
    ),
    PyPowerwallSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-charging",
        value_fn=lambda d: d["json"].get("battery"),
    ),
    PyPowerwallSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:transmission-tower",
        value_fn=lambda d: d["json"].get("grid"),
    ),
    PyPowerwallSensorDescription(
        key="home_power",
        translation_key="home_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
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
        suggested_display_precision=1,
        value_fn=lambda d: round(d["json"]["soe"], 1)
        if d["json"].get("soe") is not None
        else None,
    ),
    PyPowerwallSensorDescription(
        key="battery_reserve",
        translation_key="battery_reserve",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-lock",
        value_fn=lambda d: d["json"].get("reserve"),
    ),
    PyPowerwallSensorDescription(
        key="time_remaining",
        translation_key="time_remaining",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:timer-outline",
        value_fn=lambda d: round(d["json"]["time_remaining_hours"], 2)
        if d["json"].get("time_remaining_hours") is not None
        else None,
    ),
    # Grid
    PyPowerwallSensorDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d["aggregates"]
        .get("site", {})
        .get("instant_average_voltage"),
    ),
    PyPowerwallSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_vitals_frequency,
    ),
    # Alerts
    PyPowerwallSensorDescription(
        key="alert_count",
        translation_key="alert_count",
        icon="mdi:alert-circle-outline",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: sum(
            len(v.get("alerts", []))
            for v in (d.get("vitals") or {}).values()
            if isinstance(v, dict)
        ),
    ),
    # Troubleshooting
    PyPowerwallSensorDescription(
        key="troubleshooting_problems",
        translation_key="troubleshooting_problems",
        icon="mdi:wrench",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: len(d.get("troubleshooting") or [])
        if d.get("troubleshooting") is not None
        else None,
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
        translation_key="pod_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: round(
            d.get("POD_nom_energy_remaining", 0)
            / max(d.get("POD_nom_full_pack_energy", 1), 1)
            * 100,
            1,
        ),
    ),
    VitalsSensorDescription(
        key="pod_energy_remaining",
        translation_key="pod_energy_remaining",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-outline",
        value_fn=lambda d: d.get("POD_nom_energy_remaining"),
    ),
    VitalsSensorDescription(
        key="pod_energy_to_charge",
        translation_key="pod_energy_to_charge",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-charging-outline",
        value_fn=lambda d: d.get("POD_nom_energy_to_be_charged"),
    ),
    VitalsSensorDescription(
        key="pod_full_energy",
        translation_key="pod_full_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("POD_nom_full_pack_energy"),
    ),
)


# ---------------------------------------------------------------------------
#  Inverter sensors (per TEPINV device)
# ---------------------------------------------------------------------------
INVERTER_SENSORS: tuple[VitalsSensorDescription, ...] = (
    VitalsSensorDescription(
        key="inverter_power",
        translation_key="inverter_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("PINV_Pout"),
    ),
    VitalsSensorDescription(
        key="inverter_voltage",
        translation_key="inverter_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("PINV_Vout"),
    ),
    VitalsSensorDescription(
        key="inverter_frequency",
        translation_key="inverter_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.get("PINV_Fout"),
    ),
    VitalsSensorDescription(
        key="inverter_state",
        translation_key="inverter_state",
        icon="mdi:state-machine",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("PINV_State"),
    ),
    VitalsSensorDescription(
        key="inverter_vsplit1",
        translation_key="inverter_vsplit1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("PINV_VSplit1"),
    ),
    VitalsSensorDescription(
        key="inverter_vsplit2",
        translation_key="inverter_vsplit2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("PINV_VSplit2"),
    ),
)


# ---------------------------------------------------------------------------
#  PVAC output sensors (on primary Powerwall device)
# ---------------------------------------------------------------------------
PVAC_OUTPUT_SENSORS: tuple[VitalsSensorDescription, ...] = (
    VitalsSensorDescription(
        key="pvac_output_power",
        translation_key="pvac_output_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("PVAC_Pout"),
    ),
    VitalsSensorDescription(
        key="pvac_output_voltage",
        translation_key="pvac_output_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("PVAC_Vout"),
    ),
    VitalsSensorDescription(
        key="pvac_vl1_ground",
        translation_key="pvac_vl1_ground",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("PVAC_VL1Ground"),
    ),
    VitalsSensorDescription(
        key="pvac_vl2_ground",
        translation_key="pvac_vl2_ground",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("PVAC_VL2Ground"),
    ),
    VitalsSensorDescription(
        key="pvac_state",
        translation_key="pvac_state",
        icon="mdi:state-machine",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("PVAC_State"),
    ),
)


# ---------------------------------------------------------------------------
#  Grid meter sensors (per TEMSA device)
# ---------------------------------------------------------------------------
GRID_METER_SENSORS: tuple[VitalsSensorDescription, ...] = (
    VitalsSensorDescription(
        key="grid_l1_power",
        translation_key="grid_l1_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("METER_Z_CTA_InstRealPower"),
    ),
    VitalsSensorDescription(
        key="grid_l2_power",
        translation_key="grid_l2_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("METER_Z_CTB_InstRealPower"),
    ),
    VitalsSensorDescription(
        key="grid_l1_voltage",
        translation_key="grid_l1_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("METER_Z_VL1N"),
    ),
    VitalsSensorDescription(
        key="grid_l2_voltage",
        translation_key="grid_l2_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("METER_Z_VL2N"),
    ),
    VitalsSensorDescription(
        key="grid_l1_current",
        translation_key="grid_l1_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("METER_Z_CTA_I"),
    ),
    VitalsSensorDescription(
        key="grid_l2_current",
        translation_key="grid_l2_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("METER_Z_CTB_I"),
    ),
    VitalsSensorDescription(
        key="grid_l1_reactive_power",
        translation_key="grid_l1_reactive_power",
        native_unit_of_measurement="var",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("METER_Z_CTA_InstReactivePower"),
    ),
    VitalsSensorDescription(
        key="grid_l2_reactive_power",
        translation_key="grid_l2_reactive_power",
        native_unit_of_measurement="var",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("METER_Z_CTB_InstReactivePower"),
    ),
    VitalsSensorDescription(
        key="grid_lifetime_energy_export",
        translation_key="grid_lifetime_energy_export",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("METER_Z_LifetimeEnergyExport"),
    ),
    VitalsSensorDescription(
        key="grid_lifetime_energy_import",
        translation_key="grid_lifetime_energy_import",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("METER_Z_LifetimeEnergyImport"),
    ),
)


# ---------------------------------------------------------------------------
#  Island controller sensors (TESYNC device)
# ---------------------------------------------------------------------------
ISLAND_SENSORS: tuple[VitalsSensorDescription, ...] = (
    VitalsSensorDescription(
        key="island_freq_l1_main",
        translation_key="island_freq_l1_main",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_FreqL1_Main"),
    ),
    VitalsSensorDescription(
        key="island_freq_l2_main",
        translation_key="island_freq_l2_main",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_FreqL2_Main"),
    ),
    VitalsSensorDescription(
        key="island_freq_l1_load",
        translation_key="island_freq_l1_load",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_FreqL1_Load"),
    ),
    VitalsSensorDescription(
        key="island_freq_l2_load",
        translation_key="island_freq_l2_load",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_FreqL2_Load"),
    ),
    VitalsSensorDescription(
        key="island_voltage_l1_main",
        translation_key="island_voltage_l1_main",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_VL1N_Main"),
    ),
    VitalsSensorDescription(
        key="island_voltage_l2_main",
        translation_key="island_voltage_l2_main",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_VL2N_Main"),
    ),
    VitalsSensorDescription(
        key="island_voltage_l1_load",
        translation_key="island_voltage_l1_load",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_VL1N_Load"),
    ),
    VitalsSensorDescription(
        key="island_voltage_l2_load",
        translation_key="island_voltage_l2_load",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ISLAND_VL2N_Load"),
    ),
    VitalsSensorDescription(
        key="island_grid_state",
        translation_key="island_grid_state",
        icon="mdi:transmission-tower",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("ISLAND_GridState"),
    ),
)


# ---------------------------------------------------------------------------
#  PV string field definitions (A–F)
# ---------------------------------------------------------------------------
STRING_FIELDS = (
    (
        "power",
        "Power",
        UnitOfPower.WATT,
        SensorDeviceClass.POWER,
        "mdi:solar-power",
        0,
    ),
    (
        "voltage",
        "Voltage",
        UnitOfElectricPotential.VOLT,
        SensorDeviceClass.VOLTAGE,
        None,
        1,
    ),
    (
        "current",
        "Current",
        UnitOfElectricCurrent.AMPERE,
        SensorDeviceClass.CURRENT,
        None,
        2,
    ),
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
            pv_key = f"PVAC_PVMeasured{self._field_key}_{self._string_id}"
            if self._field_key == "Current":
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
        entities.append(PyPowerwallSensor(coordinator, entry_id, desc))

    vitals = coordinator.data.get("vitals") or {}
    block_by_serial = build_block_by_serial(coordinator.data)
    device_labels = build_device_labels(block_by_serial)

    # --- Battery pod sensors (TEPOD) ---
    for vkey, vdata in vitals.items():
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
    for vkey, vdata in vitals.items():
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
    for vkey, vdata in vitals.items():
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
    for vkey, vdata in vitals.items():
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
