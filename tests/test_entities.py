"""Entities created from real captured proxy data, plus pure helper functions."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.pypowerwall.entity import (
    build_block_by_serial,
    build_device_labels,
    parse_pod_data,
    parse_vitals_key,
)

from .conftest import load_fixture

GATEWAY = "TG0000000001AA"
FOLLOWERS = {"TG0000000002BB", "TG0000000003CC"}
EXPANSION = "TG0000000004DD"
METER = "GF0000000001MM"


# --------------------------------------------------------------------------- helpers
def test_parse_vitals_key():
    assert parse_vitals_key("TEPOD--1707000-21-K--TG0000000001AA") == ("1707000-21-K", "TG0000000001AA")
    assert parse_vitals_key("TESYNC----") == ("", "tesync")
    assert parse_vitals_key("STSTSM--1707000-21-K--TG0000000001AA")[1] == GATEWAY
    assert parse_vitals_key("weird") == ("", "weird")


def test_build_device_labels_from_fixture():
    vitals = load_fixture("vitals.json")
    system_status = load_fixture("api_system_status.json")
    blocks = build_block_by_serial({"system_status": system_status})
    assert set(blocks) == {GATEWAY, *FOLLOWERS, EXPANSION}
    labels = build_device_labels(blocks, vitals)
    assert labels[GATEWAY] == "Primary"
    assert {labels[s] for s in FOLLOWERS} == {"Follower"}
    assert labels[EXPANSION] == "Expansion"


def test_build_device_labels_without_gateway_falls_back_to_first():
    blocks = {"A": {"Type": "ACPW"}, "B": {"Type": "ACPW"}, "X": {"Type": "Expansion"}}
    labels = build_device_labels(blocks, vitals={})
    assert labels == {"A": "Primary", "B": "Follower", "X": "Expansion"}


def test_build_device_labels_single_block():
    assert build_device_labels({"A": {"Type": "ACPW"}}, {}) == {"A": "Primary"}


def test_parse_pod_data_groups_by_prefix():
    pods = parse_pod_data(load_fixture("pod.json"))
    assert set(pods) == {GATEWAY, *FOLLOWERS, EXPANSION}
    one = pods[GATEWAY]
    assert "PackageSerialNumber" in one
    assert not any(k.startswith("PW") for k in one)  # prefix stripped
    assert parse_pod_data(None) == {}
    assert parse_pod_data({"time": 1}) == {}


# --------------------------------------------------------------------------- platforms
async def test_devices_created_for_each_component(hass, proxy, setup_entry):
    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, setup_entry.entry_id)
    idents = {next(iter(d.identifiers))[1]: d for d in devices}

    # hub + 4 pods + meter + tesync
    assert setup_entry.entry_id in idents
    for serial in (GATEWAY, *FOLLOWERS, EXPANSION, METER, f"{setup_entry.entry_id}_tesync"):
        assert serial in idents, f"missing device for {serial}"
    assert "(Primary)" in idents[GATEWAY].name
    assert "(Expansion)" in idents[EXPANSION].name
    assert idents[METER].name.startswith("Grid Meter")
    # every sub device hangs off the hub
    hub = idents[setup_entry.entry_id]
    for serial in (GATEWAY, EXPANSION, METER):
        assert idents[serial].via_device_id == hub.id


async def test_main_sensors_have_values(hass, proxy, setup_entry):
    agg = load_fixture("aggregates.json")
    js = load_fixture("json.json")
    assert float(hass.states.get("sensor.pypowerwall_solar_power").state) == js["solar"]
    assert float(hass.states.get("sensor.pypowerwall_home_power").state) == pytest.approx(js["home"])
    assert float(hass.states.get("sensor.pypowerwall_battery_level").state) == round(js["soe"], 1)
    assert float(hass.states.get("sensor.pypowerwall_grid_voltage").state) == agg["site"]["instant_average_voltage"]
    assert hass.states.get("sensor.pypowerwall_operation_mode").state == "self_consumption"
    assert hass.states.get("sensor.pypowerwall_firmware_version").state == load_fixture("version.json")["version"]
    st = hass.states.get("sensor.pypowerwall_active_alerts")
    assert st is not None and st.state != "unknown"


async def test_no_duplicate_unique_ids(hass, proxy, setup_entry):
    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, setup_entry.entry_id)
    uids = [e.unique_id for e in entries]
    assert len(uids) == len(set(uids))
    assert len(entries) > 100  # 3 PW + expansion + strings + meter -> lots of entities


async def test_control_entities_only_with_secret(hass, proxy, make_entry):
    entry = make_entry(secret="")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.pypowerwall_backup_reserve") is None
    assert hass.states.get("select.pypowerwall_operation_mode") is None
    assert hass.states.get("switch.pypowerwall_grid_charging") is None
    # read-only sensors still there
    assert hass.states.get("sensor.pypowerwall_operation_mode") is not None


async def test_control_entities_reflect_proxy_state_and_send_commands(hass, proxy, setup_entry):
    exp = load_fixture("control_grid_export.json")["grid_export"]
    assert hass.states.get("select.pypowerwall_grid_export").state == exp
    assert hass.states.get("select.pypowerwall_operation_mode").state == "self_consumption"
    reserve = load_fixture("api_operation.json")["backup_reserve_percent"]
    assert float(hass.states.get("number.pypowerwall_backup_reserve").state) == reserve

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.pypowerwall_backup_reserve", "value": 33}, blocking=True,
    )
    assert ("/control/reserve", {"value": "33", "token": "s3cret"}) in proxy.posts
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.pypowerwall_operation_mode", "option": "backup"}, blocking=True,
    )
    assert ("/control/mode", {"value": "backup", "token": "s3cret"}) in proxy.posts
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.pypowerwall_grid_charging"}, blocking=True
    )
    assert ("/control/grid_charging", {"value": "false", "token": "s3cret"}) in proxy.posts


async def test_binary_sensors_from_fixture(hass, proxy, setup_entry):
    assert hass.states.get("binary_sensor.pypowerwall_grid_connected").state == "on"
    assert hass.states.get("binary_sensor.pypowerwall_proxy_degraded").state == "off"
