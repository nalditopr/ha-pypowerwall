from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PyPowerwallCoordinator

GATEWAY_MODELS = {
    "1118431": "Backup Gateway 1",
    "1152100": "Backup Gateway 2",
    "1232100": "Backup Gateway 2",
    "1841000": "Backup Switch",
    "1707000": "Powerwall+ Gateway",
    "1707001": "Powerwall 3 Gateway",
    "1841100": "Powerwall 3 Gateway",
}


def gateway_device_info(coordinator: PyPowerwallCoordinator, entry_id: str) -> DeviceInfo:
    """DeviceInfo for the hub device: the Tesla gateway (from /api/status when available)."""
    data = coordinator.data or {}
    status = data.get("gateway_status") or {}
    version = (data.get("version_info") or {}).get("version") or status.get("version")
    din = status.get("din") or ""
    # DIN looks like "1707000-21-K--TG1234567890AB" (part number -- serial)
    part, _, serial = din.rpartition("--") if "--" in din else ("", "", din)
    model = GATEWAY_MODELS.get(part.split("-")[0], "Backup Gateway") if part else "Backup Gateway"
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="PyPowerwall",
        manufacturer="Tesla",
        model=model,
        serial_number=serial or None,
        hw_version=part or None,
        sw_version=version,
        configuration_url=coordinator.base_url,
    )


class PyPowerwallEntity(CoordinatorEntity[PyPowerwallCoordinator]):
    """Base entity for PyPowerwall integration."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_device_info = gateway_device_info(coordinator, entry_id)


def parse_vitals_key(key: str) -> tuple[str, str]:
    """Return (part_number, serial) from a vitals key like TEPOD--1707000-21-K--TG12...

    For TESYNC devices (key contains TESYNC----), returns ("", "tesync").
    """
    if key.startswith("TESYNC"):
        return "", "tesync"
    parts = key.split("--")
    serial = parts[-1] if len(parts) >= 3 else key
    part_number = parts[1] if len(parts) >= 2 else ""
    return part_number, serial


def build_block_by_serial(coordinator_data: dict[str, Any]) -> dict[str, dict]:
    """Build serial -> battery block lookup from system_status."""
    battery_blocks = (
        coordinator_data.get("system_status", {}).get("battery_blocks") or []
    )
    block_by_serial: dict[str, dict] = {}
    for block in battery_blocks:
        s = block.get("PackageSerialNumber")
        if s:
            block_by_serial[s] = block
    return block_by_serial


def build_device_labels(
    block_by_serial: dict[str, dict],
    vitals: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Determine Primary/Follower/Expansion label for each serial.

    Uses the STSTSM gateway serial from vitals to definitively identify the
    Primary Powerwall.  The STSTSM device has ``STSTSM-Location: "Gateway"``
    and its serial matches the leader Powerwall's PVAC/TEPOD/TEPINV serial.

    For serials found in vitals but NOT in block_by_serial (e.g. PVAC/TEPINV
    devices), the label is inherited from the matching block serial or defaults
    to "Primary" when the serial matches the gateway.
    """
    labels: dict[str, str] = {}
    vitals = vitals or {}

    # 1. Find gateway serial from STSTSM key in vitals
    gateway_serial: str | None = None
    for vkey in vitals:
        if vkey.startswith("STSTSM"):
            _, gateway_serial = parse_vitals_key(vkey)
            break

    # 2. Label battery blocks
    non_expansion: list[str] = []
    for serial, block in block_by_serial.items():
        block_type = block.get("Type", "")
        if "Expansion" in block_type:
            labels[serial] = "Expansion"
        else:
            non_expansion.append(serial)

    if gateway_serial:
        # Definitive: gateway serial is Primary, rest are Followers
        for serial in non_expansion:
            labels[serial] = "Primary" if serial == gateway_serial else "Follower"
    elif len(non_expansion) == 1:
        labels[non_expansion[0]] = "Primary"
    elif len(non_expansion) > 1:
        # Fallback: first non-expansion is Primary
        labels[non_expansion[0]] = "Primary"
        for serial in non_expansion[1:]:
            labels[serial] = "Follower"

    # 3. Label vitals-only serials (PVAC, TEPINV without a battery_block)
    for vkey in vitals:
        if vkey.startswith(("PVAC", "TEPINV", "TEPOD")):
            _, serial = parse_vitals_key(vkey)
            if serial not in labels:
                if serial == gateway_serial:
                    labels[serial] = "Primary"
                else:
                    labels[serial] = labels.get(serial, "Follower")

    return labels


def parse_pod_data(pod_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Parse flat /pod response into per-powerwall dicts.

    The /pod endpoint returns a flat dict with PW1_, PW2_ prefixed keys.
    Returns {serial: {key_without_prefix: value, ...}} for each group.
    """
    if not pod_data:
        return {}

    result: dict[str, dict[str, Any]] = {}
    prefixes: set[str] = set()
    for key in pod_data:
        if key.startswith("PW") and "_" in key:
            prefix = key[: key.index("_") + 1]
            prefixes.add(prefix)

    for prefix in sorted(prefixes):
        pw_data: dict[str, Any] = {}
        for key, value in pod_data.items():
            if key.startswith(prefix):
                pw_data[key[len(prefix) :]] = value
        serial = pw_data.get("PackageSerialNumber", "")
        if serial:
            result[serial] = pw_data

    return result


TESYNC_PLACEHOLDER = "tesync"


def device_identifier(entry_id: str, serial: str) -> tuple[str, str]:
    """Device registry identifier for a vitals device.

    Real serials are globally unique. The TESYNC island controller has no
    serial in its vitals key (``TESYNC----``), so it is scoped to the config
    entry; otherwise two gateways/proxies would share one 'Sync Controller'.
    """
    if serial == TESYNC_PLACEHOLDER:
        return (DOMAIN, f"{entry_id}_{TESYNC_PLACEHOLDER}")
    return (DOMAIN, serial)
