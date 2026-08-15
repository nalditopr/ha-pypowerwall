"""Shared fixtures: an in-process fake pypowerwall proxy backed by captured JSON."""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pypowerwall.const import (
    CONF_CONTROL_SECRET,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "s3cret"

# path -> fixture file
ROUTES: dict[str, str] = {
    "/aggregates": "aggregates.json",
    "/vitals": "vitals.json",
    "/health": "health.json",
    "/json": "json.json",
    "/version": "version.json",
    "/api/operation": "api_operation.json",
    "/api/system_status": "api_system_status.json",
    "/api/system_status/soe": None,  # synthesised
    "/api/sitemaster": "api_sitemaster.json",
    "/pod": "pod.json",
    "/api/troubleshooting/problems": "api_troubleshooting_problems.json",
    "/stats": "stats.json",
    "/api/status": "api_status.json",
    "/api/system_status/grid_status": "api_system_status_grid_status.json",
    "/api/meters/site": "api_meters_site.json",
    "/api/meters/solar": None,  # synthesised: empty on tedapi/v1r transports
    "/control/grid_charging": "control_grid_charging.json",
    "/control/grid_export": "control_grid_export.json",
    "/control/max_backup": "control_max_backup.json",
}


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeProxy:
    """Programmable fake pypowerwall proxy."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {
            path: load_fixture(f) for path, f in ROUTES.items() if f
        }
        self.data["/api/system_status/soe"] = {"percentage": 42.0}
        self.data["/api/meters/solar"] = {}
        self.status_overrides: dict[str, int] = {}  # path -> http status
        self.control_secret: str | None = SECRET  # None -> control disabled (404)
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.get_log: list[str] = []
        self.hang: set[str] = set()  # paths that never answer

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", self._get)
        app.router.add_route("POST", "/{tail:.*}", self._post)
        return app

    async def _get(self, request: web.Request) -> web.StreamResponse:
        path = "/" + request.match_info["tail"]
        self.get_log.append(path)
        if path in self.hang:
            import asyncio

            await asyncio.sleep(3600)
        if path in self.status_overrides:
            return web.Response(status=self.status_overrides[path], text="error")
        if path.startswith("/control/") and self.control_secret is None:
            return web.Response(status=404, text="not found")
        if path not in self.data:
            return web.Response(status=404, text="not found")
        return web.json_response(self.data[path])

    async def _post(self, request: web.Request) -> web.StreamResponse:
        path = "/" + request.match_info["tail"]
        form = dict(await request.post())
        self.posts.append((path, form))
        if self.control_secret is None:
            return web.Response(status=404, text="not found")
        if form.get("token") != self.control_secret:
            return web.json_response(
                {"unauthorized": "Control Command Token Invalid"}, status=401
            )
        # mimic the proxy: empty value on /control/mode just returns current mode
        return web.json_response({"ok": True})


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def proxy(hass, aiohttp_client, socket_enabled):
    """Start the fake proxy and route the integration's shared session to it."""
    fake = FakeProxy()
    client = await aiohttp_client(fake.app())
    fake.host = client.host
    fake.port = client.port

    class _Session:
        """Route URLs for the fake proxy through the test client; anything else
        goes to a real aiohttp session (real connection errors, alt hostnames)."""

        def __init__(self, tc):
            self._tc = tc
            self._real = aiohttp.ClientSession()
            self._bases = {
                f"http://{client.host}:{client.port}",
                f"http://localhost:{client.port}",
            }

        def _route(self, url: str):
            for base in self._bases:
                if url.startswith(base):
                    return self._tc, url[len(base) :]
            return self._real, url

        def get(self, url, **kw):
            s, u = self._route(url)
            return s.get(u, **kw)

        def post(self, url, **kw):
            s, u = self._route(url)
            return s.post(u, **kw)

        async def close(self):
            await self._real.close()

    session = _Session(client)
    with (
        patch(
            "custom_components.pypowerwall.coordinator.async_get_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.pypowerwall.config_flow.async_get_clientsession",
            return_value=session,
        ),
    ):
        yield fake
    await session.close()


@pytest.fixture
def make_entry(hass, proxy) -> Callable[..., MockConfigEntry]:
    def _make(secret: str = SECRET, options: dict | None = None, **data) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="PyPowerwall (test)",
            unique_id=f"{proxy.host}:{proxy.port}",
            data={
                CONF_HOST: proxy.host,
                CONF_PORT: proxy.port,
                CONF_SCAN_INTERVAL: 30,
                CONF_CONTROL_SECRET: secret,
                **data,
            },
            options=options or {},
        )
        entry.add_to_hass(hass)
        return entry

    return _make


@pytest.fixture
async def setup_entry(hass, make_entry):
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
