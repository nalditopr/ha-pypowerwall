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
        self, hass: HomeAssistant, host: str, port: int, scan_interval: int = DEFAULT_SCAN_INTERVAL
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self._base_url = f"http://{host}:{port}"
        _LOGGER.debug(
            "PyPowerwallCoordinator initialised, base_url=%s, interval=%ss",
            self._base_url,
            scan_interval,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Starting data refresh from %s", self._base_url)
        connector = aiohttp.TCPConnector(force_close=True)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                data = await self._get(session, "/json")
                aggregates = await self._get(session, "/api/meters/aggregates")
                vitals = await self._get(session, "/vitals")
                health = await self._get(session, "/health")
                version_info = await self._get(session, "/version")
                operation = await self._get(session, "/api/operation")
                system_status = await self._get(session, "/api/system_status")
                sitemaster = await self._get(session, "/api/sitemaster")
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching pypowerwall data")
            raise UpdateFailed(f"Unexpected error: {err}") from err

        return {
            "json": data,
            "aggregates": aggregates,
            "vitals": vitals,
            "health": health,
            "version_info": version_info,
            "operation": operation,
            "system_status": system_status,
            "sitemaster": sitemaster,
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
