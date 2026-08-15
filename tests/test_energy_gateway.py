"""Energy Dashboard sensors, grid frequency source, gateway device, capacity, diagnostics."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.pypowerwall.diagnostics import async_get_config_entry_diagnostics
from custom_components.pypowerwall.sensor_descriptions import (
    _capacity_pct,
    _grid_frequency,
    _pack_count,
    observed_pack_count,
)

from .conftest import load_fixture


async def test_grid_energy_sensors_from_meters_site(hass, proxy, setup_entry):
    meters = load_fixture("api_meters_site.json")
    site = {
        f: sum(m["Cached_readings"][f] for m in meters if m.get("location") == "site")
        for f in ("energy_imported", "energy_exported")
    }
    imp = hass.states.get("sensor.pypowerwall_grid_energy_imported")
    exp = hass.states.get("sensor.pypowerwall_grid_energy_exported")
    assert imp is not None and exp is not None
    # native Wh, displayed in kWh (suggested unit) -> compare in kWh
    assert float(imp.state) == pytest.approx(site["energy_imported"] / 1000, rel=1e-6)
    assert float(exp.state) == pytest.approx(site["energy_exported"] / 1000, rel=1e-6)
    assert imp.attributes["state_class"] == "total_increasing"
    assert imp.attributes["device_class"] == "energy"
    assert imp.attributes["unit_of_measurement"] == "kWh"


async def test_energy_sensors_without_data_are_not_created(hass, proxy, setup_entry):
    """On tedapi/v1r transports /aggregates energy fields are 0 -> no entity, not 'unknown'."""
    agg = load_fixture("aggregates.json")
    assert agg["solar"]["energy_exported"] == 0  # precondition from real capture
    assert hass.states.get("sensor.pypowerwall_solar_energy_produced") is None
    assert hass.states.get("sensor.pypowerwall_battery_energy_charged") is None
    assert hass.states.get("sensor.pypowerwall_home_energy_consumed") is None


async def test_energy_sensors_created_when_aggregates_have_data(hass, proxy, make_entry):
    proxy.data["/aggregates"]["solar"]["energy_exported"] = 123456.0
    proxy.data["/aggregates"]["battery"]["energy_imported"] = 2000.0
    proxy.data["/aggregates"]["battery"]["energy_exported"] = 1500.0
    proxy.data["/aggregates"]["load"]["energy_imported"] = 99000.0
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.pypowerwall_solar_energy_produced").state) == pytest.approx(123.456)
    assert float(hass.states.get("sensor.pypowerwall_battery_energy_charged").state) == pytest.approx(2.0)
    assert float(hass.states.get("sensor.pypowerwall_battery_energy_discharged").state) == pytest.approx(1.5)
    assert float(hass.states.get("sensor.pypowerwall_home_energy_consumed").state) == pytest.approx(99.0)


async def test_energy_zero_mid_run_keeps_last_value_not_reset(hass, proxy, setup_entry):
    """A transient 0 from the proxy must not reset a TOTAL_INCREASING sensor to 0."""
    before = hass.states.get("sensor.pypowerwall_grid_energy_imported").state
    for meter in proxy.data["/api/meters/site"]:
        meter["Cached_readings"]["energy_imported"] = 0
    await setup_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    st = hass.states.get("sensor.pypowerwall_grid_energy_imported").state
    assert st in ("unknown", before)  # never "0.0"


def test_grid_frequency_prefers_site_meter_then_tesync_then_blocks_then_pvac():
    vitals = load_fixture("vitals.json")
    ss = load_fixture("api_system_status.json")
    tesync = next(v for k, v in vitals.items() if k.startswith("TESYNC"))
    # real capture: top-level f_out is null on this firmware -> TESYNC wins
    assert ss.get("f_out") is None
    assert _grid_frequency({"system_status": ss, "vitals": vitals}) == tesync["ISLAND_FreqL1_Main"]
    # explicit site-level f_out wins over everything
    assert _grid_frequency({"system_status": {**ss, "f_out": 60.01}, "vitals": vitals}) == 60.01
    # no TESYNC -> average of per-block f_out
    no_tesync = {k: v for k, v in vitals.items() if not k.startswith("TESYNC")}
    blocks = [b["f_out"] for b in ss["battery_blocks"] if b.get("f_out")]
    assert _grid_frequency({"system_status": ss, "vitals": no_tesync}) == round(sum(blocks) / len(blocks), 3)
    # nothing but PVAC
    pvac_only = {k: v for k, v in vitals.items() if k.startswith("PVAC")}
    assert _grid_frequency({"system_status": {}, "vitals": pvac_only}) == next(iter(pvac_only.values()))["PVAC_Fout"]
    assert _grid_frequency({"system_status": {}, "vitals": {}}) is None


async def test_grid_frequency_sensor_uses_grid_side_source(hass, proxy, setup_entry):
    vitals = load_fixture("vitals.json")
    tesync = next(v for k, v in vitals.items() if k.startswith("TESYNC"))
    assert float(hass.states.get("sensor.pypowerwall_grid_frequency").state) == pytest.approx(tesync["ISLAND_FreqL1_Main"])


def test_capacity_health():
    ss = load_fixture("api_system_status.json")
    vitals = load_fixture("vitals.json")
    tepods = sum(1 for k in vitals if k.startswith("TEPOD"))
    assert tepods == 4
    assert _pack_count({"system_status": ss, "vitals": vitals}) == 4
    pct = _capacity_pct({"system_status": ss, "vitals": vitals})
    assert pct == pytest.approx(round(ss["nominal_full_pack_energy"] / (13500 * 4) * 100, 1))
    # live bug: battery_blocks only listed 2 of 4 packs -> must not report ~212 %
    partial = {**ss, "battery_blocks": ss["battery_blocks"][:2]}
    assert _capacity_pct({"system_status": partial, "vitals": vitals}) == pct
    # without vitals: max of available_blocks and the blocks list
    assert _pack_count({"system_status": {**ss, "available_blocks": 3}, "vitals": {}}) == max(3, len(ss["battery_blocks"]))
    assert _pack_count({"system_status": {**ss, "available_blocks": None}, "vitals": {}}) == len(ss["battery_blocks"])
    assert _capacity_pct({"system_status": {}}) is None
    assert _capacity_pct({"system_status": {"nominal_full_pack_energy": 1, "battery_blocks": []}}) is None


async def test_site_capacity_and_island_sensors(hass, proxy, setup_entry):
    ss = load_fixture("api_system_status.json")
    assert float(hass.states.get("sensor.pypowerwall_battery_full_capacity").state) == pytest.approx(ss["nominal_full_pack_energy"] / 1000)
    assert float(hass.states.get("sensor.pypowerwall_battery_energy_remaining").state) == pytest.approx(ss["nominal_energy_remaining"] / 1000)
    assert hass.states.get("sensor.pypowerwall_island_state").state == ss["system_island_state"]
    assert int(float(hass.states.get("sensor.pypowerwall_available_powerwalls").state)) == ss["available_blocks"]


async def test_hub_device_is_the_gateway(hass, proxy, setup_entry):
    status = load_fixture("api_status.json")
    dev_reg = dr.async_get(hass)
    hub = dev_reg.async_get_device(identifiers={("pypowerwall", setup_entry.entry_id)})
    assert hub is not None
    assert hub.manufacturer == "Tesla"
    assert hub.serial_number == status["din"].split("--")[-1]
    assert hub.hw_version == status["din"].split("--")[0]
    assert hub.sw_version == load_fixture("version.json")["version"]
    assert hub.model == "Powerwall+ Gateway"
    assert hub.configuration_url == f"http://{proxy.host}:{proxy.port}"


async def test_hub_device_without_api_status_still_works(hass, proxy, make_entry):
    del proxy.data["/api/status"]
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    dev_reg = dr.async_get(hass)
    hub = dev_reg.async_get_device(identifiers={("pypowerwall", entry.entry_id)})
    assert hub.model == "Backup Gateway"
    assert hub.serial_number is None
    assert hub.sw_version == load_fixture("version.json")["version"]


async def test_diagnostics_redacts_secret_and_hosts(hass, proxy, setup_entry):
    diag = await async_get_config_entry_diagnostics(hass, setup_entry)
    assert diag["entry"]["data"]["control_secret"] == "**REDACTED**"
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["coordinator"]["has_control_secret"] is True
    # payload present but transport hosts hidden
    transports = diag["data"]["health"]["transports"]
    assert all(t.get("host") in (None, "**REDACTED**") for t in transports.values())
    assert "TG0000000001AA" in str(diag["data"]["vitals"])  # serials are kept (needed for support)


async def test_pack_count_is_sticky_when_transport_degrades(hass, proxy, setup_entry):
    """Live case: with wifi_tedapi degraded the gateway reported only 2 of 4 packs
    (vitals had 2 TEPODs, battery_blocks 2, available_blocks 3) -> capacity read 212 %."""
    from custom_components.pypowerwall.const import CONF_PACK_COUNT

    coordinator = setup_entry.runtime_data.coordinator
    assert coordinator.data["pack_count"] == 4
    assert setup_entry.options[CONF_PACK_COUNT] == 4
    healthy_pct = float(hass.states.get("sensor.pypowerwall_battery_capacity_health").state)

    # degrade: keep only 2 TEPODs in vitals and 2 blocks
    vitals = proxy.data["/vitals"]
    tepods = [k for k in vitals if k.startswith("TEPOD")]
    for k in tepods[2:]:
        del vitals[k]
    proxy.data["/api/system_status"]["battery_blocks"] = proxy.data["/api/system_status"]["battery_blocks"][:2]
    proxy.data["/api/system_status"]["available_blocks"] = 3
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert observed_pack_count(coordinator.data) == 3  # what this poll shows
    assert coordinator.data["pack_count"] == 4  # sticky
    assert float(hass.states.get("sensor.pypowerwall_battery_capacity_health").state) == healthy_pct


async def test_pack_count_survives_reload(hass, proxy, setup_entry):
    from custom_components.pypowerwall.const import CONF_PACK_COUNT

    assert setup_entry.options[CONF_PACK_COUNT] == 4
    # proxy now only shows 2 packs at (re)start -> remembered value wins
    vitals = proxy.data["/vitals"]
    for k in [k for k in vitals if k.startswith("TEPOD")][2:]:
        del vitals[k]
    proxy.data["/api/system_status"]["battery_blocks"] = proxy.data["/api/system_status"]["battery_blocks"][:2]
    proxy.data["/api/system_status"]["available_blocks"] = 2
    await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.runtime_data.coordinator.data["pack_count"] == 4


def test_pack_count_helpers():
    assert _pack_count({"pack_count": 4, "vitals": {}}) == 4
    assert _pack_count({"vitals": {"TEPOD--a": {}, "TEPOD--b": {}}, "system_status": {"available_blocks": 1}}) == 2
    assert observed_pack_count({"vitals": {}, "system_status": {"available_blocks": 3, "battery_blocks": [{}, {}]}}) == 3
    assert observed_pack_count({}) == 0
