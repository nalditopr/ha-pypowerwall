"""Coordinator: endpoint fan-out, optional/required handling, auth, failures."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
import pytest

from custom_components.pypowerwall.coordinator import ENDPOINTS


@pytest.fixture
def tick(hass):
    """Trigger a coordinator refresh cycle deterministically."""

    async def _tick(entry):
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

    return _tick


async def test_setup_fetches_every_endpoint_once(hass, proxy, setup_entry):
    coordinator = setup_entry.runtime_data.coordinator
    assert setup_entry.state is ConfigEntryState.LOADED
    fetched = set(proxy.get_log)
    for _key, path, _req in ENDPOINTS:
        assert path in fetched, f"{path} was not polled"
    # each endpoint exactly once during first refresh
    assert len(proxy.get_log) == len(ENDPOINTS)
    d = coordinator.data
    assert d["json"]["soe"] is not None
    assert d["aggregates"]["site"]["instant_power"] is not None
    assert d["operation"]["real_mode"] == "self_consumption"
    assert d["control_grid_export"]["grid_export"] in ("battery_ok", "pv_only", "never")
    assert d["gateway_status"]["din"].startswith("1707000")


async def test_optional_endpoint_404_yields_none_or_empty(hass, proxy, make_entry):
    del proxy.data["/pod"]
    del proxy.data["/api/system_status"]  # dict-default key
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    d = entry.runtime_data.coordinator.data
    assert d["pod"] is None
    assert d["system_status"] == {}


async def test_control_disabled_on_proxy_yields_none(hass, proxy, make_entry):
    proxy.control_secret = None  # proxy without PW_CONTROL_SECRET -> 404 on /control/*
    entry = make_entry(secret="")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    d = entry.runtime_data.coordinator.data
    assert d["control_grid_charging"] is None
    assert d["control_max_backup"] is None
    assert entry.state is ConfigEntryState.LOADED


async def test_required_endpoint_error_fails_setup(hass, proxy, make_entry):
    proxy.status_overrides["/vitals"] = 500
    entry = make_entry()
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unauthorized_triggers_reauth(hass, proxy, make_entry):
    proxy.status_overrides["/control/grid_export"] = 401
    entry = make_entry()
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler("pypowerwall")
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_required_failure_after_setup_marks_update_failed_then_recovers(
    hass, proxy, setup_entry, tick
):
    coordinator = setup_entry.runtime_data.coordinator
    proxy.status_overrides["/aggregates"] = 503
    await tick(setup_entry)
    assert not coordinator.last_update_success
    assert hass.states.get("sensor.pypowerwall_solar_power").state == "unavailable"

    del proxy.status_overrides["/aggregates"]
    await tick(setup_entry)
    assert coordinator.last_update_success
    assert hass.states.get("sensor.pypowerwall_solar_power").state != "unavailable"


async def test_all_core_endpoints_empty_is_failure(hass, proxy, make_entry):
    for p in ("/aggregates", "/vitals", "/health"):
        proxy.data[p] = {}
    entry = make_entry()
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_uses_shared_session_not_a_new_one_per_poll(hass, proxy, setup_entry, tick):
    """The coordinator must reuse HA's shared aiohttp session (no per-poll sessions)."""
    coordinator = setup_entry.runtime_data.coordinator
    session_before = coordinator._session
    await tick(setup_entry)
    await tick(setup_entry)
    assert coordinator._session is session_before


async def test_send_command_posts_value_and_token(hass, proxy, setup_entry):
    coordinator = setup_entry.runtime_data.coordinator
    assert await coordinator.send_command("/control/reserve", 20)
    assert proxy.posts[-1] == ("/control/reserve", {"value": "20", "token": "s3cret"})


async def test_send_command_bad_token_returns_false(hass, proxy, make_entry):
    entry = make_entry(secret="wrong")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert not await coordinator.send_command("/control/reserve", 20)


async def test_send_command_without_secret_is_refused_locally(hass, proxy, make_entry):
    entry = make_entry(secret="")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert not await coordinator.send_command("/control/reserve", 20)
    assert proxy.posts == []
