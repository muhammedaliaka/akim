"""Panelin telefon/PC uygulaması olarak yüklenebilmesi için gereken parçalar (manifest, simgeler, servis çalışanı)."""

import asyncio
import json
import re
import struct
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from akim.config import WebConfig
from akim.web.server import STATIC, create_app
from tests.test_engine import build


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "geçerli PNG değil"
    return struct.unpack(">II", data[16:24])


def with_client(fn):
    async def run():
        engine, _ = build()
        client = TestClient(TestServer(create_app(engine, WebConfig())))
        await client.start_server()
        try:
            await fn(client)
        finally:
            await client.close()
            await engine.http.close()

    asyncio.run(run())


def test_manifest_is_installable_and_every_icon_resolves():
    async def check(client):
        r = await client.get("/manifest.webmanifest")
        assert r.status == 200 and r.content_type == "application/manifest+json"
        m = json.loads(await r.text())
        assert m["display"] == "standalone" and m["start_url"] == "/" and m["short_name"] == "Akım"
        assert re.fullmatch(r"#[0-9a-f]{6}", m["theme_color"]) and re.fullmatch(r"#[0-9a-f]{6}", m["background_color"])
        sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        # Chrome/Edge yüklenebilirlik: 192 ve 512 piksel PNG; ayrıca Android'in yuvarlatabildiği maskable simge
        assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes
        for icon in m["icons"]:
            res = await client.get(icon["src"])
            assert res.status == 200, icon["src"]
            body = await res.read()
            if icon["type"] == "image/png":
                w, h = icon["sizes"].split("x")
                assert png_size(body) == (int(w), int(h)), icon["src"]
            else:
                assert b"<svg" in body

    with_client(check)


def test_service_worker_is_root_scoped_and_never_caches_live_data():
    async def check(client):
        r = await client.get("/sw.js")
        assert r.status == 200 and "javascript" in r.content_type
        assert r.headers["Cache-Control"] == "no-cache" and r.headers["Service-Worker-Allowed"] == "/"
        js = await r.text()
        assert '"/api/"' in js and "startsWith" in js, "canlı veri önbelleğe alınmamalı"

    with_client(check)


def test_dashboard_declares_mobile_and_pwa_support():
    async def check(client):
        r = await client.get("/")
        html = await r.text()
        assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
        assert 'rel="manifest"' in html and "viewport-fit=cover" in html and 'name="theme-color"' in html
        assert "safe-area-inset" in html and "visibilitychange" in html and 'serviceWorker.register("/sw.js")' in html
        # Android Chrome'da `new Notification()` kullanılamaz; servis çalışanı yolu olmalı
        assert "showNotification" in html

    with_client(check)


def test_static_route_blocks_path_traversal(tmp_path):
    async def check(client):
        for bad in ("/static/../config.example.yaml", "/static/%2e%2e/pyproject.toml", "/static/..%2fserver.py"):
            r = await client.get(bad)
            assert r.status in (403, 404), bad
        assert (await client.get("/static/icon.svg")).status == 200

    with_client(check)


def test_static_assets_ship_in_the_package_data():
    names = {p.name for p in STATIC.iterdir()}
    assert {"icon.svg", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "sw.js", "manifest.webmanifest"} <= names
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "web/static/*" in pyproject, "pip ile kurulumda simgeler pakete girmeli"
