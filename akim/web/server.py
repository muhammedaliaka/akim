"""Canlı web paneli ve JSON API (aiohttp)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from ..config import WebConfig
from ..engine import Engine
from ..runtime import lan_addresses

log = logging.getLogger(__name__)
DASHBOARD = Path(__file__).with_name("dashboard.html")
STATIC = Path(__file__).with_name("static")


def _json(data, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str), status=status, content_type="application/json"
    )


def create_app(engine: Engine, cfg: WebConfig) -> web.Application:
    app = web.Application()

    def authorized(request: web.Request) -> bool:
        if not cfg.token:
            return True
        header = request.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {cfg.token}")

    async def index(_: web.Request) -> web.Response:
        return web.Response(
            text=DASHBOARD.read_text(encoding="utf-8"), content_type="text/html", headers={"Cache-Control": "no-cache"}
        )

    async def manifest(_: web.Request) -> web.Response:
        # Android'de "Ana ekrana ekle / Uygulamayı yükle", Windows'ta Edge/Chrome "Uygulama olarak yükle"
        return web.Response(
            body=(STATIC / "manifest.webmanifest").read_bytes(),
            content_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    async def service_worker(_: web.Request) -> web.Response:
        # Kök kapsamda servis edilmeli; her zaman yeniden doğrulanır ki güncellemeler gecikmesin
        return web.Response(
            body=(STATIC / "sw.js").read_bytes(),
            content_type="text/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    async def healthz(_: web.Request) -> web.Response:
        last = max(engine.last_run.values(), default=engine.started_at)
        return _json({"ok": True, "uptime": time.time() - engine.started_at, "last_activity": last})

    async def status(_: web.Request) -> web.Response:
        st = engine.status()
        st["auth_required"] = bool(cfg.token)
        return _json(st)

    async def board(request: web.Request) -> web.Response:
        decisions = [d for d in request.query.get("decision", "").split(",") if d] or None
        limit = min(500, int(request.query.get("limit", "200")))
        return _json(engine.board(decisions, limit))

    async def game(request: web.Request) -> web.Response:
        detail = engine.game_detail(request.match_info["key"])
        return _json(detail) if detail else _json({"error": "bulunamadı"}, 404)

    async def upcoming(request: web.Request) -> web.Response:
        limit = min(200, int(request.query.get("limit", "50")))
        return _json(engine.upcoming_board(limit))

    async def alerts(request: web.Request) -> web.Response:
        limit = min(500, int(request.query.get("limit", "100")))
        return _json(engine.store.recent_alerts(limit))

    async def check(request: web.Request) -> web.Response:
        if not authorized(request):
            return _json({"error": "yetkisiz"}, 401)
        body = await request.json()
        title = str(body.get("title", "")).strip()[:200]
        if not title:
            return _json({"error": "title gerekli"}, 400)
        try:
            return _json(await engine.adhoc_check(title))
        except Exception as exc:
            log.warning("Anlık kontrol başarısız: %s", exc)
            return _json({"error": str(exc)}, 502)

    async def rescan(request: web.Request) -> web.Response:
        if not authorized(request):
            return _json({"error": "yetkisiz"}, 401)
        key = request.match_info["key"]
        if not engine.store.get_game(key):
            return _json({"error": "bulunamadı"}, 404)
        try:
            await engine.scan_roblox(key)
        except Exception as exc:
            log.warning("Yeniden tarama başarısız (%s): %s", key, exc)
            return _json({"error": str(exc)}, 502)
        return _json(engine.game_detail(key))

    async def events(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
        await resp.prepare(request)
        q = engine.bus.subscribe()
        try:
            await resp.write(b": bagli\n\n")
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=20)
                    payload = json.dumps(data, ensure_ascii=False, default=str)
                    await resp.write(f"event: {event}\ndata: {payload}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            engine.bus.unsubscribe(q)
        return resp

    app.router.add_get("/", index)
    app.router.add_get("/manifest.webmanifest", manifest)
    app.router.add_get("/sw.js", service_worker)
    app.router.add_static("/static/", STATIC, follow_symlinks=False)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/board", board)
    app.router.add_get("/api/games/{key:.+}", game)
    app.router.add_get("/api/upcoming", upcoming)
    app.router.add_get("/api/alerts", alerts)
    app.router.add_get("/api/events", events)
    app.router.add_post("/api/check", check)
    app.router.add_post("/api/rescan/{key:.+}", rescan)
    return app


async def start_web(engine: Engine, cfg: WebConfig) -> web.AppRunner:
    app = create_app(engine, cfg)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    if cfg.host in ("0.0.0.0", "", "::"):
        log.info("Web paneli: http://localhost:%d", cfg.port)
        for ip in lan_addresses():
            log.info("Telefondan (aynı Wi-Fi): http://%s:%d", ip, cfg.port)
    else:
        log.info("Web paneli: http://%s:%d", cfg.host, cfg.port)
    return runner
