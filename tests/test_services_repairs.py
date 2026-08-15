"""Services, persisted max-backup duration, expired max-backup handling, repair issues."""
from __future__ import annotations

import time

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
import pytest
import voluptuous as vol

from custom_components.pypowerwall.const import CONF_MAX_BACKUP_MINUTES, DOMAIN

from .conftest import SECRET


async def _refresh(hass, entry):
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


# ------------------------------------------------------------------ services
async def test_services_registered_and_send_commands(hass, proxy, setup_entry):
    for svc in ("set_reserve", "set_mode", "set_grid_export", "set_grid_charging", "start_max_backup", "cancel_max_backup"):
        assert hass.services.has_service(DOMAIN, svc), svc

    await hass.services.async_call(DOMAIN, "set_reserve", {"reserve": 42}, blocking=True)
    assert ("/control/reserve", {"value": "42", "token": SECRET}) in proxy.posts
    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "backup"}, blocking=True)
    assert ("/control/mode", {"value": "backup", "token": SECRET}) in proxy.posts
    await hass.services.async_call(DOMAIN, "set_grid_export", {"policy": "pv_only"}, blocking=True)
    assert ("/control/grid_export", {"value": "pv_only", "token": SECRET}) in proxy.posts
    await hass.services.async_call(DOMAIN, "set_grid_charging", {"enabled": False}, blocking=True)
    assert ("/control/grid_charging", {"value": "false", "token": SECRET}) in proxy.posts
    await hass.services.async_call(DOMAIN, "start_max_backup", {"minutes": 90}, blocking=True)
    assert ("/control/max_backup", {"value": str(90 * 60), "token": SECRET}) in proxy.posts
    await hass.services.async_call(DOMAIN, "cancel_max_backup", {}, blocking=True)
    assert ("/control/max_backup", {"value": "cancel", "token": SECRET}) in proxy.posts


async def test_service_validation_errors(hass, proxy, setup_entry):
    with pytest.raises(vol.Invalid):  # out of range
        await hass.services.async_call(DOMAIN, "set_reserve", {"reserve": 150}, blocking=True)
    with pytest.raises(vol.Invalid):  # bad mode
        await hass.services.async_call(DOMAIN, "set_mode", {"mode": "party"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_reserve", {"reserve": 10, "config_entry_id": "nope"}, blocking=True)


async def test_service_without_control_secret_is_rejected(hass, proxy, make_entry):
    entry = make_entry(secret="")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_reserve", {"reserve": 10}, blocking=True)
    assert proxy.posts == []


async def test_service_proxy_rejection_raises(hass, proxy, setup_entry):
    proxy.control_secret = "rotated-on-proxy"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "set_reserve", {"reserve": 10}, blocking=True)


async def test_start_max_backup_uses_persisted_default_and_cancels_lingering(hass, proxy, setup_entry):
    coordinator = setup_entry.runtime_data.coordinator
    # default duration
    await hass.services.async_call(DOMAIN, "start_max_backup", {}, blocking=True)
    assert proxy.posts[-1] == ("/control/max_backup", {"value": str(coordinator.max_backup_minutes * 60), "token": SECRET})
    # lingering expired event -> cancel first, then schedule
    proxy.data["/control/max_backup"] = {"manual_backup": {"active": False, "end_time": time.time() - 10}, "backup_events": []}
    await _refresh(hass, setup_entry)
    proxy.posts.clear()
    await hass.services.async_call(DOMAIN, "start_max_backup", {"minutes": 30}, blocking=True)
    assert [p[1]["value"] for p in proxy.posts if p[0] == "/control/max_backup"] == ["cancel", "1800"]


# ------------------------------------------------- max backup duration persistence
async def test_max_backup_duration_persists_across_reload(hass, proxy, setup_entry):
    ent = "number.pypowerwall_max_backup_duration"
    assert float(hass.states.get(ent).state) == 60  # default
    await hass.services.async_call("number", "set_value", {"entity_id": ent, "value": 120}, blocking=True)
    await hass.async_block_till_done()
    assert setup_entry.options[CONF_MAX_BACKUP_MINUTES] == 120
    assert setup_entry.state is ConfigEntryState.LOADED  # option change must NOT reload

    await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(ent).state) == 120
    assert setup_entry.runtime_data.coordinator.max_backup_duration == 7200


async def test_scan_interval_option_change_still_reloads(hass, proxy, setup_entry):
    before = setup_entry.runtime_data.coordinator
    hass.config_entries.async_update_entry(setup_entry, options={**setup_entry.options, "scan_interval": 45})
    await hass.async_block_till_done()
    after = setup_entry.runtime_data.coordinator
    assert after is not before
    assert after.update_interval.total_seconds() == 45


# ------------------------------------------------------- expired max backup
async def test_max_backup_switch_off_when_event_expired(hass, proxy, setup_entry):
    sw = "switch.pypowerwall_max_backup"
    assert hass.states.get(sw).state == "off"  # fixture: manual_backup null

    now = time.time()
    proxy.data["/control/max_backup"] = {"manual_backup": {"active": True, "start_time": now, "end_time": now + 600, "duration_seconds": 600}, "backup_events": []}
    await _refresh(hass, setup_entry)
    st = hass.states.get(sw)
    assert st.state == "on"
    assert st.attributes["duration_seconds"] == 600

    # gateway leaves the event lingering after expiry -> must read OFF
    proxy.data["/control/max_backup"] = {"manual_backup": {"active": False, "start_time": now - 700, "end_time": now - 100}, "backup_events": []}
    await _refresh(hass, setup_entry)
    assert hass.states.get(sw).state == "off"

    # no 'active' flag from an older proxy -> fall back to end_time
    proxy.data["/control/max_backup"] = {"manual_backup": {"start_time": now - 700, "end_time": now - 100}, "backup_events": []}
    await _refresh(hass, setup_entry)
    assert hass.states.get(sw).state == "off"
    proxy.data["/control/max_backup"] = {"manual_backup": {"start_time": now, "end_time": now + 100}, "backup_events": []}
    await _refresh(hass, setup_entry)
    assert hass.states.get(sw).state == "on"


async def test_max_backup_get_carries_token_so_proxy_can_purge_expired(hass, proxy, setup_entry):
    """The proxy only auto-cancels lingering events on GET when ?token= is valid."""
    # our fake logs the path without query; check the raw request instead
    assert any(p == "/control/max_backup" for p in proxy.get_log)
    assert proxy.last_query.get("/control/max_backup") == {"token": SECRET}


async def test_max_backup_switch_turn_on_cancels_lingering_first(hass, proxy, setup_entry):
    now = time.time()
    proxy.data["/control/max_backup"] = {"manual_backup": {"active": False, "end_time": now - 5}, "backup_events": []}
    await _refresh(hass, setup_entry)
    proxy.posts.clear()
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.pypowerwall_max_backup"}, blocking=True)
    assert [p[1]["value"] for p in proxy.posts if p[0] == "/control/max_backup"] == ["cancel", "3600"]


# ------------------------------------------------------------------ repairs
async def test_repair_issue_when_proxy_degraded_and_cleared(hass, proxy, setup_entry):
    reg = ir.async_get(hass)
    issue_id = f"proxy_degraded_{proxy.host}_{proxy.port}"
    assert reg.async_get_issue(DOMAIN, issue_id) is None

    proxy.data["/health"]["connection_health"]["is_degraded"] = True
    await _refresh(hass, setup_entry)
    issue = reg.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "proxy_degraded"
    assert issue.severity == ir.IssueSeverity.WARNING

    proxy.data["/health"]["connection_health"]["is_degraded"] = False
    await _refresh(hass, setup_entry)
    assert reg.async_get_issue(DOMAIN, issue_id) is None


async def test_repair_issue_fallback_mode_and_unload_clears(hass, proxy, setup_entry):
    reg = ir.async_get(hass)
    issue_id = f"proxy_fallback_{proxy.host}_{proxy.port}"
    proxy.data["/health"]["fallback_mode"]["is_fallback_mode"] = True
    await _refresh(hass, setup_entry)
    assert reg.async_get_issue(DOMAIN, issue_id) is not None

    await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get_issue(DOMAIN, issue_id) is None
