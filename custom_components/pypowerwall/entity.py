from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PyPowerwallCoordinator


class PyPowerwallEntity(CoordinatorEntity[PyPowerwallCoordinator]):
    """Base entity for PyPowerwall integration."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PyPowerwallCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="PyPowerwall",
            manufacturer="Tesla",
            model="Powerwall",
        )


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


def build_device_labels(block_by_serial: dict[str, dict]) -> dict[str, str]:
    """Determine Primary/Follower/Expansion label for each serial.

    Logic:
    - "Expansion" if Type contains "Expansion"
    - If only 1 non-expansion → "Primary"
    - If multiple non-expansion: Type containing "Solar" → "Primary", rest → "Follower"
    - Fallback: first non-expansion is Primary, rest Follower
    """
    labels: dict[str, str] = {}
    non_expansion: list[str] = []

    for serial, block in block_by_serial.items():
        block_type = block.get("Type", "")
        if "Expansion" in block_type:
            labels[serial] = "Expansion"
        else:
            non_expansion.append(serial)

    if len(non_expansion) == 1:
        labels[non_expansion[0]] = "Primary"
    elif len(non_expansion) > 1:
        primary_found = False
        for serial in non_expansion:
            block_type = block_by_serial[serial].get("Type", "")
            if "Solar" in block_type and not primary_found:
                labels[serial] = "Primary"
                primary_found = True
            else:
                labels[serial] = "Follower"
        # Fallback: if no Solar type found, first is Primary
        if not primary_found:
            labels[non_expansion[0]] = "Primary"
            for serial in non_expansion[1:]:
                labels[serial] = "Follower"

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
