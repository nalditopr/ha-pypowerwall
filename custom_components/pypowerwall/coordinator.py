from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_MAX_BACKUP_MINUTES, DEFAULT_MAX_BACKUP_MINUTES, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# (result key, path, required)
# Required endpoints raise UpdateFailed on error; optional ones resolve to None.
ENDPOINTS: tuple[tuple[str, str, bool], ...] = (
    ("aggregates", "/aggregates", True),
    ("vitals", "/vitals", True),
    ("health", "/health", True),
    ("json", "/json", False),
    ("version_info", "/version", False),
    ("operation", "/api/operation", False),
    ("system_status", "/api/system_status", False),
    ("sitemaster", "/api/sitemaster", False),
    ("pod", "/pod", False),
    ("troubleshooting", "/api/troubleshooting/problems", False),
    ("stats", "/stats", False),
    ("gateway_status", "/api/status", False),
    ("grid_status", "/api/system_status/grid_status", False),
    ("meters_site", "/api/meters/site", False),
    ("meters_solar", "/api/meters/solar", False),
    # Control state (only meaningful when PW_CONTROL_SECRET is set on the proxy)
    ("control_grid_charging", "/control/grid_charging", False),
    ("control_grid_export", "/control/grid_export", False),
    ("control_max_backup", "/control/max_backup", False),
)

# Keys whose value should default to {} rather than None when missing.
DICT_DEFAULT_KEYS = frozenset(
    {"json", "aggregates", "vitals", "health", "version_info", "system_status", "sitemaster"}
)


class PyPowerwallCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls the pypowerwall proxy."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        control_secret: str = "",
        max_backup_minutes: int = DEFAULT_MAX_BACKUP_MINUTES,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=config_entry,
        )
        self.host = host
        self.port = port
        self._base_url = f"http://{host}:{port}"
        self._control_secret = control_secret
        self._session = async_get_clientsession(hass)
        self._max_backup_minutes = int(max_backup_minutes)
        _LOGGER.debug(
            "Coordinator initialised: base_url=%s interval=%ss control=%s",
            self._base_url,
            scan_interval,
            "enabled" if control_secret else "disabled",
        )

    @property
    def base_url(self) -> str:
        """Return the proxy base URL."""
        return self._base_url

    @property
    def has_control_secret(self) -> bool:
        """Return True if a control secret is configured."""
        return bool(self._control_secret)

    # ------------------------------------------------------- max backup duration
    @property
    def max_backup_minutes(self) -> int:
        """Duration used when the max-backup switch is turned on."""
        return self._max_backup_minutes

    def set_max_backup_minutes(self, minutes: int, *, persist: bool = True) -> None:
        """Set (and by default persist in the config entry options) the max-backup duration."""
        self._max_backup_minutes = int(minutes)
        entry = self.config_entry
        if persist and entry is not None and entry.options.get(CONF_MAX_BACKUP_MINUTES) != int(minutes):
            self.hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_MAX_BACKUP_MINUTES: int(minutes)}
            )

    def matches(self, scan_interval: int, control_secret: str) -> bool:
        """True if this coordinator was built with these connection settings."""
        running = int(self.update_interval.total_seconds()) if self.update_interval else None
        return running == int(scan_interval) and self._control_secret == control_secret

    @property
    def max_backup_duration(self) -> int:
        """Duration in seconds (what the proxy expects)."""
        return self._max_backup_minutes * 60

    # ------------------------------------------------------- max backup state
    @property
    def max_backup_active(self) -> bool | None:
        """True while a manual (max) backup event is active, None if unknown.

        The gateway leaves expired events lingering, so 'manual_backup present'
        is not enough: use its 'active' flag (or end_time when missing).
        """
        data = self.data.get("control_max_backup") if self.data else None
        if not isinstance(data, dict) or "manual_backup" not in data:
            return None
        mb = data.get("manual_backup")
        if not mb:
            return False
        if "active" in mb:
            return bool(mb["active"])
        end = mb.get("end_time")
        if end is not None:
            return time.time() < float(end)
        return True

    def _path_for(self, path: str) -> str:
        """Add the control token to endpoints where the proxy uses it for GET.

        /control/max_backup: with a valid token the proxy auto-cancels expired
        manual backup events that the gateway leaves lingering; a plain GET is
        read-only and keeps reporting the stale event.
        """
        if path == "/control/max_backup" and self._control_secret:
            return f"{path}?token={self._control_secret}"
        return path

    async def _async_update_data(self) -> dict[str, Any]:
        results = await asyncio.gather(
            *(self._fetch(self._path_for(path), required) for _key, path, required in ENDPOINTS)
        )
        data: dict[str, Any] = {}
        for (key, _path, _required), value in zip(ENDPOINTS, results, strict=True):
            data[key] = value if value is not None or key not in DICT_DEFAULT_KEYS else {}

        # Failsafe: if all core endpoints came back empty, the proxy is not usable.
        if not any(data[k] for k in ("aggregates", "vitals", "health")):
            raise UpdateFailed("All core endpoints returned no data")

        _LOGGER.debug(
            "Refreshed %d endpoints from %s (%d without data)",
            len(ENDPOINTS),
            self._base_url,
            sum(1 for v in results if v is None),
        )
        self._update_repairs(data)
        return data

    # ------------------------------------------------------------------ repairs
    def _update_repairs(self, data: dict[str, Any]) -> None:
        """Raise / clear repair issues from /health."""
        health = data.get("health") or {}
        degraded = bool((health.get("connection_health") or {}).get("is_degraded"))
        fallback = bool((health.get("fallback_mode") or {}).get("is_fallback_mode"))
        self._set_issue("proxy_degraded", degraded, {"host": self.host})
        self._set_issue("proxy_fallback", fallback, {"host": self.host})

    def _set_issue(self, key: str, active: bool, placeholders: dict[str, str]) -> None:
        issue_id = f"{key}_{self.host}_{self.port}"
        if active:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=key,
                translation_placeholders=placeholders,
                learn_more_url="https://github.com/jasonacox/pypowerwall/tree/main/proxy#health-check",
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _fetch(self, path: str, required: bool) -> Any:
        """GET one proxy endpoint and return its parsed JSON.

        Required endpoints raise UpdateFailed on any failure. Optional
        endpoints return None on 404 / errors so the rest of the update
        can proceed. A 401/403 always means the proxy rejected our request
        (bad or missing control secret) -> reauth.
        """
        url = f"{self._base_url}{path}"
        path = path.split("?", 1)[0]  # never log / raise the query string (may carry the token)
        try:
            async with self._session.get(url, timeout=REQUEST_TIMEOUT) as resp:
                if resp.status in (401, 403):
                    raise ConfigEntryAuthFailed(
                        f"pypowerwall proxy rejected request to {path} (HTTP {resp.status})"
                    )
                if resp.status == 404 and not required:
                    return None
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except ConfigEntryAuthFailed:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            if required:
                raise UpdateFailed(
                    f"Error communicating with pypowerwall proxy ({path}): {err}"
                ) from err
            _LOGGER.debug("Optional endpoint %s unavailable: %s", path, err)
            return None
        except ValueError as err:  # invalid JSON
            if required:
                raise UpdateFailed(f"Invalid JSON from {path}: {err}") from err
            _LOGGER.debug("Optional endpoint %s returned invalid JSON: %s", path, err)
            return None

    async def send_command(self, path: str, value: str | int | float) -> bool:
        """POST a control command to the proxy.

        Uses form data with value + token as expected by pypowerwall proxy:
          curl -X POST -d "value=VALUE&token=SECRET" http://host:port/control/...
        """
        if not self._control_secret:
            _LOGGER.error("Control secret not configured - cannot send command")
            return False
        url = f"{self._base_url}{path}"
        form_data = {"value": str(value), "token": self._control_secret}
        _LOGGER.debug("POST %s value=%s", url, value)
        try:
            async with self._session.post(
                url, data=form_data, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    _LOGGER.error(
                        "POST %s rejected (HTTP %s): check the control secret",
                        url,
                        resp.status,
                    )
                    return False
                resp.raise_for_status()
                return True
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("POST %s failed: %s", url, err)
            return False
