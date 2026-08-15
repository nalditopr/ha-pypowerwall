# ha-pypowerwall

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacsbadge]][hacs]

<img src="brand/icon.png" alt="PyPowerwall icon" width="96" align="right">

Home Assistant custom integration for Tesla Powerwall via [pypowerwall][pypowerwall] proxy.

[![Add Integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=pypowerwall)

## How It Works

```
Home Assistant
    │
    │  aiohttp HTTP GET/POST (no Python pypowerwall library needed)
    ▼
pypowerwall proxy (default :8675)
    │
    ▼
Tesla Powerwall gateway
```

No Python pypowerwall library required in HA — pure `aiohttp`.

**This integration will set up the following platforms —**

| Platform        | Description                                                                              |
| --------------- | ---------------------------------------------------------------------------------------- |
| `sensor`        | Power, battery, grid, inverter, PVAC, PV string, island controller, and grid meter data  |
| `binary_sensor` | Grid status, alerts, pod health flags, PV string connected                               |
| `number`        | Backup reserve percentage control (requires `control_secret`)                            |
| `select`        | Operation mode and grid export policy (requires `control_secret`)                        |
| `switch`        | Grid charging toggle (requires `control_secret`)                                         |

## Features

- **Multi-Powerwall support** — Primary, Follower, and Expansion pod detection
- **Per-device sensors** — TEPOD (battery), TEPINV (inverter), PVAC (solar combiner), PVS strings A–F, TEMSA (grid meter), TESYNC (island controller)
- **Grid meter per-leg data** — L1/L2 power, voltage, current, reactive power, and lifetime energy counters
- **Pod health flags** — from `/pod` endpoint (permanently/persistently faulted, active heating, charge/discharge complete, wobble detected, and more)
- **Alert monitoring** — per-device and aggregate alert counts
- **Backup reserve, operation mode, grid charging & grid export control** — requires `PW_CONTROL_SECRET` configured on the proxy
- **Configurable polling interval** — 5–300 seconds (default 30 s)
- **Energy Dashboard ready** — lifetime grid import/export (from the site meter), and solar / battery / home counters when the proxy reports them (`sensor.pypowerwall_*_energy_*`, `total_increasing`)
- **Gateway device** — the hub device carries the gateway DIN, part number and firmware; sub-devices per Powerwall / meter / island controller
- **Battery capacity health** — usable full-pack energy vs. nominal (13.5 kWh per Powerwall) as %
- **Reconfigure & reauth** — change host/port/secret from the UI; the proxy rejecting the control secret starts a reauth flow
- **Diagnostics** — download redacted diagnostics from the device page
- **Services** — `pypowerwall.set_reserve`, `set_mode`, `set_grid_export`, `set_grid_charging`, `start_max_backup`, `cancel_max_backup` for automations (require the control secret)
- **Repairs** — a repair issue is raised while the proxy reports a degraded connection or fallback mode, and cleared when it recovers

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open the ha-pypowerwall integration inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nalditopr&repository=ha-pypowerwall&category=integration)

Or manually add the custom repository:

1. Open HACS in Home Assistant.
2. Go to **Integrations** → three-dot menu → **Custom repositories**.
3. Add `https://github.com/nalditopr/ha-pypowerwall` with category **Integration**.
4. Search for **PyPowerwall** and install.
5. Restart Home Assistant.

### Manual

1. Copy `custom_components/pypowerwall/` into your HA `custom_components/` directory.
2. Restart Home Assistant.

### Configuration

1. Go to **Settings → Devices & Services → Add Integration** and search for **PyPowerwall**.
2. Enter your pypowerwall proxy **host** and **port** (default: `8675`).
3. Optionally set the **polling interval** (default: 30 seconds).
4. Optionally enter the **control secret** (`PW_CONTROL_SECRET`) to enable backup reserve and operation mode controls.

## Requirements

- Running [pypowerwall][pypowerwall] proxy (e.g. `pypowerwall proxy -port 8675`)
- Home Assistant 2024.12+

## Credits

This integration communicates with the [pypowerwall][pypowerwall] proxy by [@jasonacox](https://github.com/jasonacox).

---

[releases-shield]: https://img.shields.io/github/release/nalditopr/ha-pypowerwall.svg?style=for-the-badge
[releases]: https://github.com/nalditopr/ha-pypowerwall/releases
[commits-shield]: https://img.shields.io/github/commit-activity/y/nalditopr/ha-pypowerwall.svg?style=for-the-badge
[commits]: https://github.com/nalditopr/ha-pypowerwall/commits/main
[license-shield]: https://img.shields.io/github/license/nalditopr/ha-pypowerwall.svg?style=for-the-badge
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[hacs]: https://hacs.xyz
[pypowerwall]: https://github.com/jasonacox/pypowerwall
