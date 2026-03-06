from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class PyPowerwallCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls the pypowerwall proxy."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        control_secret: str = "",
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self._base_url = f"http://{host}:{port}"
        self._control_secret = control_secret
        _LOGGER.debug(
            "PyPowerwallCoordinator initialised, base_url=%s, interval=%ss, control=%s",
            self._base_url,
            scan_interval,
            "enabled" if control_secret else "disabled",
        )

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Starting data refresh from %s", self._base_url)
        connector = aiohttp.TCPConnector(force_close=True)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                # Confirmed endpoints (required)
                aggregates = await self._get(session, "/aggregates")
                vitals = await self._get(session, "/vitals")
                health = await self._get(session, "/health")

                # Non-confirmed endpoints (optional)
                data = await self._get_optional(session, "/json")
                version_info = await self._get_optional(session, "/version")
                operation = await self._get_optional(session, "/api/operation")
                system_status = await self._get_optional(session, "/api/system_status")
                sitemaster = await self._get_optional(session, "/api/sitemaster")
                pod = await self._get_optional(session, "/pod")
                troubleshooting = await self._get_optional(
                    session, "/api/troubleshooting/problems"
                )
                stats = await self._get_optional(session, "/stats")
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching pypowerwall data")
            raise UpdateFailed(f"Unexpected error: {err}") from err

        # Failsafe: if all core endpoints return None, proxy is down
        if all(v is None for v in [aggregates, vitals, health]):
            raise UpdateFailed("All core endpoints unreachable")

        return {
            "json": data or {},
            "aggregates": aggregates or {},
            "vitals": vitals or {},
            "health": health or {},
            "version_info": version_info or {},
            "operation": operation,
            "system_status": system_status or {},
            "sitemaster": sitemaster or {},
            "pod": pod,
            "troubleshooting": troubleshooting,
            "stats": stats,
        }

    async def _get(self, session: aiohttp.ClientSession, path: str) -> Any:
        url = f"{self._base_url}{path}"
        _LOGGER.debug("GET %s", url)
        try:
            async with session.get(
                url,
                headers={"Connection": "close"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                _LOGGER.debug("Response %s: status=%s", url, resp.status)
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            _LOGGER.error("ClientError on GET %s: %s (%s)", url, err, type(err).__name__)
            raise UpdateFailed(f"Error communicating with pypowerwall proxy: {err}") from err

    async def _get_optional(self, session: aiohttp.ClientSession, path: str) -> Any:
        """Fetch an endpoint, returning None on 404 or any client error."""
        url = f"{self._base_url}{path}"
        _LOGGER.debug("GET (optional) %s", url)
        try:
            async with session.get(
                url,
                headers={"Connection": "close"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 404:
                    _LOGGER.debug("Optional endpoint %s returned 404", url)
                    return None
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            _LOGGER.debug("Optional endpoint %s failed: %s", url, err)
            return None

    @property
    def has_control_secret(self) -> bool:
        """Return True if a control secret is configured."""
        return bool(self._control_secret)

    async def send_command(self, path: str, value: str | int | float) -> bool:
        """POST a control command to the proxy.

        Uses form data with value + token as expected by pypowerwall proxy:
          curl -X POST -d "value=VALUE&token=SECRET" http://host:port/control/...
        """
        if not self._control_secret:
            _LOGGER.error("Control secret not configured — cannot send command")
            return False
        url = f"{self._base_url}{path}"
        form_data = {"value": str(value), "token": self._control_secret}
        _LOGGER.debug("POST %s value=%s", url, value)
        connector = aiohttp.TCPConnector(force_close=True)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    url,
                    data=form_data,
                    headers={"Connection": "close"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    _LOGGER.debug("POST %s: status=%s", url, resp.status)
                    resp.raise_for_status()
                    return True
        except aiohttp.ClientError as err:
            _LOGGER.error("POST %s failed: %s", url, err)
            return False
