"""Config / options / reconfigure / reauth flows."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.pypowerwall.const import (
    CONF_CONTROL_SECRET,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import SECRET


async def _start_user_flow(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_success(hass, proxy):
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: proxy.host, CONF_PORT: proxy.port, CONF_SCAN_INTERVAL: 10, CONF_CONTROL_SECRET: SECRET},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"PyPowerwall ({proxy.host}:{proxy.port})"
    assert result["data"][CONF_CONTROL_SECRET] == SECRET
    assert result["result"].unique_id == f"{proxy.host}:{proxy.port}"


async def test_user_flow_cannot_connect(hass, proxy):
    proxy.status_overrides["/api/system_status/soe"] = 500
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: proxy.host, CONF_PORT: proxy.port}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_secret(hass, proxy):
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: proxy.host, CONF_PORT: proxy.port, CONF_CONTROL_SECRET: "nope"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    # validation must not have changed anything on the proxy
    assert all(form["value"] == "" for _p, form in proxy.posts)


async def test_user_flow_control_unsupported(hass, proxy):
    proxy.control_secret = None
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: proxy.host, CONF_PORT: proxy.port, CONF_CONTROL_SECRET: "x"},
    )
    assert result["errors"] == {"base": "control_unsupported"}
    # ...but with no secret it is fine
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: proxy.host, CONF_PORT: proxy.port, CONF_CONTROL_SECRET: ""}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate_aborts(hass, proxy, setup_entry):
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: proxy.host, CONF_PORT: proxy.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_clearing_secret_disables_controls(hass, proxy, setup_entry):
    """Regression: an empty secret in options used to be ignored (is not None check)."""
    assert hass.states.get("number.pypowerwall_backup_reserve") is not None

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15, CONF_CONTROL_SECRET: ""}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.state is ConfigEntryState.LOADED
    coordinator = setup_entry.runtime_data.coordinator
    assert not coordinator.has_control_secret
    assert coordinator.update_interval.total_seconds() == 15
    # entity stays registered but is no longer provided -> unavailable
    assert hass.states.get("number.pypowerwall_backup_reserve").state == "unavailable"


async def test_options_flow_rejects_bad_secret(hass, proxy, setup_entry):
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_CONTROL_SECRET: "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_changes_port_and_unique_id(hass, proxy, setup_entry):
    result = await setup_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    # form is pre-filled with current values
    assert result["data_schema"]({})[CONF_HOST] == proxy.host

    # same fake proxy, but reached via a hostname alias -> validates, updates entry
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "localhost", CONF_PORT: proxy.port, CONF_SCAN_INTERVAL: 20, CONF_CONTROL_SECRET: SECRET},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.data[CONF_HOST] == "localhost"
    assert setup_entry.data[CONF_SCAN_INTERVAL] == 20
    assert setup_entry.unique_id == f"localhost:{proxy.port}"
    assert setup_entry.title == f"PyPowerwall (localhost:{proxy.port})"


async def test_reconfigure_cannot_connect(hass, proxy, setup_entry):
    result = await setup_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1, CONF_CONTROL_SECRET: ""}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_updates_secret(hass, proxy, make_entry):
    proxy.status_overrides["/control/grid_export"] = 401
    entry = make_entry(secret="stale")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    flow_id = flows[0]["flow_id"]

    # proxy is fixed / new secret known
    del proxy.status_overrides["/control/grid_export"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_CONTROL_SECRET: SECRET}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_CONTROL_SECRET] == SECRET
    assert entry.state is ConfigEntryState.LOADED
