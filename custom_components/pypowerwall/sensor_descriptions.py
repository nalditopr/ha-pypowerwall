"""Sensor entity descriptions and value helpers for PyPowerwall.

Kept separate from sensor.py (entity classes + platform setup) so each file
stays readable; nothing here touches Home Assistant runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
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
from homeassistant.util import dt as dt_util


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
def _grid_frequency(d: dict) -> float | None:
    """Grid-side frequency.

    Preference: system_status.f_out (site level; null on some firmwares) ->
    TESYNC ISLAND_FreqL1_Main (grid side of the island controller) ->
    per-Powerwall f_out from battery_blocks -> PVAC_Fout (solar inverter
    output, only equal to the grid while on-grid; last resort).
    """
    ss = d.get("system_status") or {}
    if ss.get("f_out"):
        return ss["f_out"]
    vitals = d.get("vitals") or {}
    for key, val in vitals.items():
        if key.startswith("TESYNC") and isinstance(val, dict) and val.get("ISLAND_FreqL1_Main"):
            return val["ISLAND_FreqL1_Main"]
    block_f = [b.get("f_out") for b in ss.get("battery_blocks") or [] if b.get("f_out")]
    if block_f:
        return round(sum(block_f) / len(block_f), 3)
    for key, val in vitals.items():
        if key.startswith("PVAC") and isinstance(val, dict):
            return val.get("PVAC_Fout")
    return None


def _site_meter_energy(d: dict, field: str) -> float | None:
    """Sum a lifetime energy field over the site meters in /api/meters/site.

    The endpoint returns a list of meters (location 'site'), each with
    Cached_readings.energy_imported/exported in Wh.
    """
    meters = d.get("meters_site")
    if not isinstance(meters, list):
        return None
    total = 0.0
    seen = False
    for m in meters:
        if not isinstance(m, dict) or m.get("location") not in (None, "site"):
            continue
        v = (m.get("Cached_readings") or {}).get(field)
        if v:
            total += float(v)
            seen = True
    return total if seen else None


def _energy(section: str, field: str):
    """Lifetime energy counter (Wh) for a meter section.

    /api/meters/site is authoritative for the site meter; /aggregates carries
    the same fields for site/battery/load/solar but is zeroed on some proxy
    transports (tedapi/v1r), so a 0 there is treated as 'unknown' and the
    entity keeps its last value instead of resetting a TOTAL_INCREASING sensor.
    """

    def _fn(d: dict) -> float | None:
        if section == "site":
            v = _site_meter_energy(d, field)
            if v:
                return v
        v = (d.get("aggregates") or {}).get(section, {}).get(field)
        return v if v else None

    return _fn


def _parse_ts(value):
    """Parse an ISO timestamp from the proxy into an aware datetime (or None)."""
    if not value:
        return None
    dt = dt_util.parse_datetime(str(value))
    if dt is None:
        return None
    # The proxy's own timestamps (health.startup_time) are naive local time.
    return dt if dt.tzinfo else dt_util.as_utc(dt.replace(tzinfo=dt_util.get_default_time_zone()))


# Energy sensors are only created when the proxy actually reports the counter
# (some transports zero /aggregates energy fields; then only /api/meters/site works).
ENERGY_SENSOR_KEYS = frozenset(
    {
        "grid_energy_imported",
        "grid_energy_exported",
        "solar_energy_produced",
        "battery_energy_charged",
        "battery_energy_discharged",
        "home_energy_consumed",
    }
)


NOMINAL_PACK_WH = 13500  # Powerwall 2 / + / 3 and their expansion packs are all 13.5 kWh nominal


def _pack_count(d: dict) -> int:
    """Number of battery packs (Powerwalls + expansions) in the system.

    `system_status.battery_blocks` is not reliable: on some firmwares it only
    lists a subset of the packs at any given poll. Prefer counting TEPOD
    devices in vitals (one per pack), then available_blocks, then the blocks
    list as a last resort.
    """
    vitals = d.get("vitals") or {}
    tepods = sum(1 for k in vitals if k.startswith("TEPOD"))
    if tepods:
        return tepods
    ss = d.get("system_status") or {}
    return ss.get("available_blocks") or len(ss.get("battery_blocks") or [])


def _capacity_pct(d: dict) -> float | None:
    """Full-pack energy as % of the system's nominal capacity (13.5 kWh per pack).

    New packs read slightly above 100 %; the value trends down with age.
    """
    ss = d.get("system_status") or {}
    full = ss.get("nominal_full_pack_energy")
    packs = _pack_count(d)
    if not full or not packs:
        return None
    return round(full / (NOMINAL_PACK_WH * packs) * 100, 1)


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
        value_fn=_grid_frequency,
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
    PyPowerwallSensorDescription(
        key="active_alerts",
        translation_key="active_alerts",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: ", ".join(sorted({
            alert
            for v in (d.get("vitals") or {}).values()
            if isinstance(v, dict)
            for alert in v.get("alerts", [])
        })) or "None",
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
    # Energy (lifetime counters -> Energy Dashboard)
    PyPowerwallSensorDescription(
        key="grid_energy_imported",
        translation_key="grid_energy_imported",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:transmission-tower-import",
        value_fn=_energy("site", "energy_imported"),
    ),
    PyPowerwallSensorDescription(
        key="grid_energy_exported",
        translation_key="grid_energy_exported",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:transmission-tower-export",
        value_fn=_energy("site", "energy_exported"),
    ),
    PyPowerwallSensorDescription(
        key="solar_energy_produced",
        translation_key="solar_energy_produced",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:solar-power-variant",
        value_fn=_energy("solar", "energy_exported"),
    ),
    PyPowerwallSensorDescription(
        key="battery_energy_charged",
        translation_key="battery_energy_charged",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:battery-arrow-up",
        value_fn=_energy("battery", "energy_imported"),
    ),
    PyPowerwallSensorDescription(
        key="battery_energy_discharged",
        translation_key="battery_energy_discharged",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:battery-arrow-down",
        value_fn=_energy("battery", "energy_exported"),
    ),
    PyPowerwallSensorDescription(
        key="home_energy_consumed",
        translation_key="home_energy_consumed",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:home-lightning-bolt-outline",
        value_fn=_energy("load", "energy_imported"),
    ),
    # Site battery capacity
    PyPowerwallSensorDescription(
        key="site_full_pack_energy",
        translation_key="site_full_pack_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:battery",
        value_fn=lambda d: (d.get("system_status") or {}).get("nominal_full_pack_energy"),
    ),
    PyPowerwallSensorDescription(
        key="site_energy_remaining",
        translation_key="site_energy_remaining",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:battery-70",
        value_fn=lambda d: (d.get("system_status") or {}).get("nominal_energy_remaining"),
    ),
    PyPowerwallSensorDescription(
        key="battery_capacity_health",
        translation_key="battery_capacity_health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-heart-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_capacity_pct,
    ),
    PyPowerwallSensorDescription(
        key="available_blocks",
        translation_key="available_blocks",
        icon="mdi:battery-multiple",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("system_status") or {}).get("available_blocks"),
    ),
    PyPowerwallSensorDescription(
        key="island_state",
        translation_key="island_state",
        icon="mdi:transmission-tower",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("system_status") or {}).get("system_island_state")
        or (d.get("grid_status") or {}).get("grid_status"),
    ),
    # Gateway
    PyPowerwallSensorDescription(
        key="gateway_uptime",
        translation_key="gateway_uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-start",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _parse_ts((d.get("gateway_status") or {}).get("start_time")),
    ),
    # Battery power envelope (system_status). Several of these are null on
    # v1r/older firmwares, so they are disabled by default; enable if populated.
    PyPowerwallSensorDescription(
        key="battery_target_power",
        translation_key="battery_target_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-sync",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("system_status") or {}).get("battery_target_power"),
    ),
    PyPowerwallSensorDescription(
        key="max_charge_power",
        translation_key="max_charge_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-up-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (d.get("system_status") or {}).get("max_charge_power"),
    ),
    PyPowerwallSensorDescription(
        key="max_discharge_power",
        translation_key="max_discharge_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-down-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (d.get("system_status") or {}).get("max_discharge_power"),
    ),
    PyPowerwallSensorDescription(
        key="instantaneous_max_charge_power",
        translation_key="instantaneous_max_charge_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-up",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (d.get("system_status") or {}).get("instantaneous_max_charge_power"),
    ),
    PyPowerwallSensorDescription(
        key="instantaneous_max_discharge_power",
        translation_key="instantaneous_max_discharge_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-down",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (d.get("system_status") or {}).get("instantaneous_max_discharge_power"),
    ),
    PyPowerwallSensorDescription(
        key="inverter_nominal_usable_power",
        translation_key="inverter_nominal_usable_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (d.get("system_status") or {}).get("inverter_nominal_usable_power"),
    ),
    PyPowerwallSensorDescription(
        key="solar_real_power_limit",
        translation_key="solar_real_power_limit",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:solar-power-variant-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (d.get("system_status") or {}).get("solar_real_power_limit"),
    ),
    # Proxy health
    PyPowerwallSensorDescription(
        key="proxy_uptime",
        translation_key="proxy_uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:server-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _parse_ts((d.get("health") or {}).get("startup_time")),
    ),
    PyPowerwallSensorDescription(
        key="proxy_data_age",
        translation_key="proxy_data_age",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:timer-sand",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: ((d.get("health") or {}).get("connection_health") or {}).get("last_success_age_seconds"),
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


