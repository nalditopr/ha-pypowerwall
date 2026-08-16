# Changelog

All notable changes to this integration are documented here.
Versions follow the `version` field in `custom_components/pypowerwall/manifest.json`.

## 0.4.2 — 2026-08-16

### Fixed
- `sensor.*_battery_reserve` now reads the same source as the backup-reserve number
  (`/api/operation.backup_reserve_percent`, falling back to `/json.reserve`); the two could differ
  by a few percent because `/json` reports pypowerwall's scaled value.
- The island controller (TESYNC) device is now identified per config entry instead of a shared
  `tesync` id, so multiple gateways/proxies no longer collapse into one "Sync Controller" device.
  Existing installs are migrated in place (same device, entities and history kept). (#8)

## 0.4.1 — 2026-08-15

### Fixed
- Battery Capacity Health could read >200 % while a proxy transport (e.g. `wifi_tedapi`) was
  degraded and the gateway reported only some of the packs. The coordinator now remembers the
  maximum pack count it has ever observed (persisted in the entry options) and per-pack values use
  that. (#7)

## 0.4.0 — 2026-08-15

### Added
- Integration icon shipped in `custom_components/pypowerwall/brand/` (HA 2026.3+ serves local
  brand images; the `home-assistant/brands` repo no longer accepts custom-integration PRs).
- One connectivity binary sensor per proxy→gateway transport from `/health.transports`
  (`v1r_lan`, `wifi_tedapi`, `lan_control`, …) plus a proxy fallback-mode binary sensor.
- Battery power envelope sensors from `system_status`: battery target power (enabled by default);
  max / instantaneous charge & discharge power, inverter nominal usable power, solar real power
  limit (disabled by default — `null` on v1r firmwares). Proxy start time and data age.
- `icons.json` for the new entities and all services.

### Changed
- Rarely-changing endpoints (`/version`, `/api/status`, `/stats`, `/api/sitemaster`,
  `/api/troubleshooting/problems`, `/api/meters/solar`) are polled every 10th refresh and carried
  over in between (~1/3 fewer requests per cycle).
- `sensor.py` split into `sensor.py` (entities/setup) and `sensor_descriptions.py`
  (descriptions/helpers). No functional change. (#6)

## 0.3.0 — 2026-08-15

### Added
- Services: `pypowerwall.set_reserve`, `set_mode`, `set_grid_export`, `set_grid_charging`,
  `start_max_backup`, `cancel_max_backup` (control secret required; `config_entry_id` when more
  than one proxy is configured).
- Repairs: warning issue while `/health` reports a degraded connection or fallback mode; cleared
  automatically on recovery / unload.

### Fixed
- Max Backup switch stayed ON after the backup event ended (the gateway leaves expired events
  lingering). It now follows the event's `active` flag (or `end_time`) and exposes
  start/end/duration attributes; the integration also passes `?token=` on the status GET so the
  proxy purges expired events.
- Max Backup Duration is persisted in the entry options and changing it no longer reloads the
  integration (only scan interval / control secret changes do).
- Battery Capacity Health counted packs from `battery_blocks`, which can be partial. (#4, #5)

## 0.2.0 — 2026-08-15

### Added
- Energy Dashboard sensors: lifetime grid import/export from `/api/meters/site`; solar / battery
  / home counters from `/aggregates` when the proxy reports them (not created when zeroed; a
  transient 0 never resets a `total_increasing` sensor).
- Reconfigure flow (host / port / interval / secret) and reauth flow when the proxy rejects the
  control secret; the secret is validated on entry.
- Hub device is the Tesla gateway (DIN → serial + hardware version, model from part number,
  firmware, link to the proxy).
- Battery full capacity, energy remaining, capacity health %, available Powerwalls, island state,
  gateway start time.
- `diagnostics.py` (redacted).
- pytest suite against an in-process fake proxy serving captured (redacted) payloads; CI with
  ruff, hassfest, HACS validation and pytest.

### Changed
- Coordinator uses Home Assistant's shared aiohttp session (was ~14 new connections per poll),
  one table-driven `_fetch`, one DEBUG line per refresh instead of 20+.
- Grid frequency comes from a grid-side source (`system_status.f_out` → TESYNC
  `ISLAND_FreqL1_Main` → mean per-block `f_out` → `PVAC_Fout`), previously the solar inverter
  output only.
- Options flow no longer stores `config_entry` in `__init__` (deprecated); an empty control secret
  in options now really disables the controls.
- HTTP 401/403 from the proxy starts reauth instead of silently disabling entities.
- Manifest/HACS metadata: GitHub URLs, `integration_type: hub`, min HA 2024.12, MIT license. (#2, #3)

## 0.1.0

Initial release: sensors, binary sensors, alert events, backup reserve / operation mode / grid
charging / grid export / max backup controls via the pypowerwall proxy.
