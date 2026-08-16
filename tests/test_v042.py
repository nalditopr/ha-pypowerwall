"""0.4.2: reserve sensor source, per-entry TESYNC device + migration."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.pypowerwall.const import DOMAIN
from custom_components.pypowerwall.entity import TESYNC_PLACEHOLDER, device_identifier

from .conftest import load_fixture


async def test_reserve_sensor_matches_number_source(hass, proxy, setup_entry):
    op = load_fixture("api_operation.json")["backup_reserve_percent"]
    assert float(hass.states.get("sensor.pypowerwall_battery_reserve").state) == op
    assert float(hass.states.get("number.pypowerwall_backup_reserve").state) == op
    # differing scaled value in /json must not leak into the sensor while operation is available
    proxy.data["/json"]["reserve"] = op + 5
    await setup_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.pypowerwall_battery_reserve").state) == op


async def test_reserve_sensor_falls_back_to_json(hass, proxy, make_entry):
    del proxy.data["/api/operation"]
    proxy.data["/json"]["reserve"] = 33
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.pypowerwall_battery_reserve").state) == 33


def test_device_identifier():
    assert device_identifier("E1", "TG123") == (DOMAIN, "TG123")
    assert device_identifier("E1", TESYNC_PLACEHOLDER) == (DOMAIN, "E1_tesync")
    assert device_identifier("E2", TESYNC_PLACEHOLDER) != device_identifier("E1", TESYNC_PLACEHOLDER)


async def test_tesync_device_is_scoped_to_entry(hass, proxy, setup_entry):
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get_device(identifiers={(DOMAIN, TESYNC_PLACEHOLDER)}) is None
    dev = dev_reg.async_get_device(identifiers={device_identifier(setup_entry.entry_id, TESYNC_PLACEHOLDER)})
    assert dev is not None
    assert dev.name == "Sync Controller"
    assert dev.serial_number is None


async def test_legacy_shared_tesync_device_is_migrated_in_place(hass, proxy, make_entry):
    entry = make_entry()
    dev_reg = dr.async_get(hass)
    # simulate a device created by <0.4.2: shared identifier, attached to this entry
    legacy = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, TESYNC_PLACEHOLDER)},
        name="Sync Controller",
        manufacturer="Tesla",
    )
    legacy_id = legacy.id
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    migrated = dev_reg.async_get_device(identifiers={device_identifier(entry.entry_id, TESYNC_PLACEHOLDER)})
    assert migrated is not None
    assert migrated.id == legacy_id, "must re-key the existing device, not create a new one"
    assert dev_reg.async_get_device(identifiers={(DOMAIN, TESYNC_PLACEHOLDER)}) is None
    # only one Sync Controller device for this entry, and its entities point at it
    ent_reg = er.async_get(hass)
    island = [e for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id) if "_tesync_" in e.unique_id]
    assert island and all(e.device_id == legacy_id for e in island)
    syncs = [d for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id) if d.name == "Sync Controller"]
    assert len(syncs) == 1
