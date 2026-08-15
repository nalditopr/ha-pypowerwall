# Architecture & design notes

This integration talks only to a running [pypowerwall proxy](https://github.com/jasonacox/pypowerwall)
over plain HTTP with Home Assistant's shared `aiohttp` session. No Python dependency on
`pypowerwall` itself.

```
Home Assistant ──HTTP──▶ pypowerwall proxy (:8675) ──▶ Tesla gateway
   coordinator            /aggregates /vitals ...        (v1r LAN / TEDAPI Wi-Fi / cloud)
```

## Files

| File | Role |
| --- | --- |
| `coordinator.py` | Polls the proxy, builds one `data` dict per refresh, raises repair issues, sends control commands. |
| `sensor_descriptions.py` | Entity descriptions + pure value helpers (no HA runtime objects). |
| `sensor.py` | Sensor entity classes and platform setup (main / per-device / PV string sensors). |
| `binary_sensor.py` | Grid, proxy, transports, alerts, pod health, PV string connectivity. |
| `number.py`, `select.py`, `switch.py` | Controls (only created when a control secret is configured). |
| `event.py` | `alert_fired` / `alert_cleared` events by diffing vitals alerts between refreshes. |
| `services.py`, `services.yaml` | Parameterised control services for automations. |
| `config_flow.py` | User / reconfigure / reauth / options flows; validates the control secret against the proxy. |
| `entity.py` | Base entity, gateway `DeviceInfo`, vitals key parsing, Primary/Follower/Expansion labelling, `/pod` parsing. |
| `diagnostics.py` | Redacted diagnostics download. |
| `brand/` | `icon.png` / `icon@2x.png` served by HA ≥ 2026.3 (`/api/brands/integration/pypowerwall/icon.png`). |

## Endpoints polled

Declared once in `coordinator.ENDPOINTS` as `(key, path, required)`:

- **Required** (any failure → `UpdateFailed`): `/aggregates`, `/vitals`, `/health`.
- **Optional** (404/error → `None`, dict-shaped keys default to `{}`): `/json`, `/version`,
  `/api/operation`, `/api/system_status`, `/api/sitemaster`, `/pod`, `/api/troubleshooting/problems`,
  `/stats`, `/api/status`, `/api/system_status/grid_status`, `/api/meters/site`, `/api/meters/solar`,
  `/control/grid_charging`, `/control/grid_export`, `/control/max_backup`.
- **Slow-polled** (`SLOW_POLL_KEYS`, every `SLOW_POLL_EVERY` = 10 refreshes, value carried over in
  between): `/version`, `/api/status`, `/stats`, `/api/sitemaster`, `/api/troubleshooting/problems`,
  `/api/meters/solar`.
- HTTP **401/403** anywhere → `ConfigEntryAuthFailed` → HA starts the reauth flow.
- `/control/max_backup` is fetched with `?token=<secret>`: per the proxy source a plain GET is
  read-only, while a token-authenticated GET makes the proxy cancel expired manual-backup events the
  gateway leaves lingering. The query string is stripped from every log line / exception.

## Things learned from real payloads (why the code looks the way it does)

- **`/api/meters/site` is a list** of meters (`location: "site"`) with `Cached_readings.energy_imported
  / energy_exported` (Wh). That is the counter that is actually populated on tedapi/v1r transports;
  `/aggregates` energy fields are zeroed there. Energy sensors whose counter is 0/absent at setup are
  **not created**; a transient 0 later never resets a `total_increasing` sensor (`None` → keeps last).
- **Grid frequency**: `system_status.f_out` is `null` on some firmwares; the TESYNC island controller's
  `ISLAND_FreqL1_Main` is grid-side; per-block `f_out` exists inside `battery_blocks`; `PVAC_Fout` is
  the *solar inverter* output and only equals the grid while on-grid → it is the last resort.
- **Pack count is unreliable during degradation**: when a transport (e.g. `wifi_tedapi`) is
  degraded, the gateway reports only a subset of packs — fewer TEPODs in vitals, fewer
  `battery_blocks`, and `available_blocks` may differ from both. The coordinator keeps the **maximum
  ever observed** in `data["pack_count"]`, persisted in entry options, and per-pack maths (capacity
  health = `nominal_full_pack_energy / (13.5 kWh × packs)`) uses that. New packs read >100 %.
- **Max backup**: `manual_backup` may be present but `active: false` after expiry → the switch uses
  `active` (or `end_time` for older proxies), and any lingering event is cancelled before scheduling.
- **Control secret validation**: `POST /control/mode` with an empty `value` makes the proxy check the
  token first and, on success, just return the current mode — nothing changes. Used by the config,
  options and reauth flows (`invalid_auth` / `control_unsupported`).
- **DIN**: `1707000-21-K--TG…` = part number `--` serial; part-number prefix → gateway model.
- **`battery_blocks` type**: `"BatteryExpansion"` for expansion packs, `""` for the leader on some
  firmwares.

## Options that do not reload

`max_backup_minutes` and `pack_count` live in the entry options but only tweak runtime state; the
update listener reloads the entry only when the effective scan interval or control secret changed.

## Testing

`tests/conftest.py` starts an in-process `aiohttp` fake proxy backed by `tests/fixtures/*.json`
(captured from a real 3× Powerwall+ + expansion system, serials/hosts redacted consistently so
cross-endpoint relationships still hold). Tests drive the real config flows, coordinator, entities
and services against it. Run: `python -m pytest -q` (see `requirements_test.txt`).
