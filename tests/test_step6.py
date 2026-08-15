"""Transport/fallback binary sensors, power-envelope sensors, slow polling."""
from __future__ import annotations

from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.pypowerwall.coordinator import ENDPOINTS, SLOW_POLL_EVERY, SLOW_POLL_KEYS

from .conftest import load_fixture


async def _refresh(hass, entry):
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


# --------------------------------------------------------------- transports
async def test_transport_binary_sensors_created_from_health(hass, proxy, setup_entry):
    transports = load_fixture("health.json")["transports"]
    assert set(transports) == {"v1r_lan", "wifi_tedapi", "lan_control"}
    for name in transports:
        st = hass.states.get(f"binary_sensor.pypowerwall_transport_{name}")
        assert st is not None, name
        assert st.state == "on"
        assert st.attributes["device_class"] == "connectivity"
        assert "host" not in st.attributes  # never leak the proxy's transport hosts
        assert st.attributes.get("status") == "ok"


async def test_transport_goes_off_and_recovers(hass, proxy, setup_entry):
    ent = "binary_sensor.pypowerwall_transport_wifi_tedapi"
    proxy.data["/health"]["transports"]["wifi_tedapi"].update({"status": "cooldown", "active": False, "cooldown_remaining_seconds": 42})
    await _refresh(hass, setup_entry)
    st = hass.states.get(ent)
    assert st.state == "off"
    assert st.attributes["cooldown_remaining_seconds"] == 42
    proxy.data["/health"]["transports"]["wifi_tedapi"].update({"status": "ok", "active": True})
    await _refresh(hass, setup_entry)
    assert hass.states.get(ent).state == "on"


async def test_transport_missing_from_health_is_unknown(hass, proxy, setup_entry):
    del proxy.data["/health"]["transports"]["lan_control"]
    await _refresh(hass, setup_entry)
    assert hass.states.get("binary_sensor.pypowerwall_transport_lan_control").state == "unknown"


async def test_no_transport_entities_when_health_has_none(hass, proxy, make_entry):
    del proxy.data["/health"]["transports"]
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    assert not [e for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id) if "_transport_" in e.unique_id]


async def test_proxy_fallback_binary_sensor(hass, proxy, setup_entry):
    ent = "binary_sensor.pypowerwall_proxy_fallback_mode"
    assert hass.states.get(ent).state == "off"
    proxy.data["/health"]["fallback_mode"].update({"is_fallback_mode": True, "fallback_since": "2026-08-15T10:00:00", "recovery_attempts": 3})
    await _refresh(hass, setup_entry)
    st = hass.states.get(ent)
    assert st.state == "on"
    assert st.attributes["recovery_attempts"] == 3


# ------------------------------------------------------- power envelope sensors
async def test_power_envelope_sensors(hass, proxy, setup_entry):
    # battery_target_power is enabled by default; fixture value is 0
    st = hass.states.get("sensor.pypowerwall_battery_target_power")
    assert st is not None and float(st.state) == 0
    # the rest are disabled by default but registered
    ent_reg = er.async_get(hass)
    for key in ("max_charge_power", "max_discharge_power", "instantaneous_max_charge_power",
                "instantaneous_max_discharge_power", "inverter_nominal_usable_power", "solar_real_power_limit"):
        e = ent_reg.async_get(f"sensor.pypowerwall_{key.replace('_', '_')}")
        assert e is not None, key
        assert e.disabled_by == er.RegistryEntryDisabler.INTEGRATION
    # proxy data age is live
    age = hass.states.get("sensor.pypowerwall_proxy_data_age")
    assert age is not None and float(age.state) >= 0


async def test_power_envelope_values_when_firmware_reports_them(hass, proxy, make_entry):
    proxy.data["/api/system_status"].update({"max_charge_power": 15000, "max_discharge_power": 21000, "battery_target_power": -3200})
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    ent_reg.async_update_entity("sensor.pypowerwall_max_charge_power", disabled_by=None)
    ent_reg.async_update_entity("sensor.pypowerwall_max_discharge_power", disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.pypowerwall_max_charge_power").state) == 15000
    assert float(hass.states.get("sensor.pypowerwall_max_discharge_power").state) == 21000
    assert float(hass.states.get("sensor.pypowerwall_battery_target_power").state) == -3200


async def test_proxy_uptime_is_aware_timestamp(hass, proxy, make_entry):
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    ent_reg.async_update_entity("sensor.pypowerwall_proxy_started", disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    st = hass.states.get("sensor.pypowerwall_proxy_started")
    assert st.state not in ("unknown", "unavailable")
    assert "+" in st.state or st.state.endswith("Z")  # tz-aware ISO string


# ------------------------------------------------------------- slow polling
async def test_slow_poll_endpoints_are_skipped_between_cycles(hass, proxy, setup_entry):
    slow_paths = {path for key, path, _r in ENDPOINTS if key in SLOW_POLL_KEYS}
    fast_paths = {path for key, path, _r in ENDPOINTS if key not in SLOW_POLL_KEYS}
    assert slow_paths and fast_paths

    # first refresh (setup) fetched everything
    assert slow_paths <= set(proxy.get_log)
    proxy.get_log.clear()

    for _ in range(SLOW_POLL_EVERY - 1):
        await _refresh(hass, setup_entry)
    got = set(proxy.get_log)
    assert fast_paths <= got
    assert not (slow_paths & got), "slow endpoints must not be polled between cycles"

    # values carried over, entities still populated
    d = setup_entry.runtime_data.coordinator.data
    assert d["version_info"]["version"] == load_fixture("version.json")["version"]
    assert hass.states.get("sensor.pypowerwall_firmware_version").state == d["version_info"]["version"]

    proxy.get_log.clear()
    await _refresh(hass, setup_entry)  # cycle SLOW_POLL_EVERY -> slow ones due again
    assert slow_paths <= set(proxy.get_log)


async def test_slow_poll_value_refreshes_when_due(hass, proxy, setup_entry):
    proxy.data["/version"] = {"version": "99.9.9", "vint": 999}
    for _ in range(SLOW_POLL_EVERY - 1):
        await _refresh(hass, setup_entry)
    assert hass.states.get("sensor.pypowerwall_firmware_version").state != "99.9.9"  # not yet due
    await _refresh(hass, setup_entry)
    assert hass.states.get("sensor.pypowerwall_firmware_version").state == "99.9.9"


@pytest.mark.parametrize("key", sorted(SLOW_POLL_KEYS))
def test_slow_poll_keys_are_real_endpoints(key):
    assert key in {k for k, _p, _r in ENDPOINTS}
