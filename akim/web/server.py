"""Canlı web paneli ve JSON API (aiohttp)."""

from __future__ import annotations

import asyncio
import fnmatch
import hmac
import ipaddress
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from ..config import WebConfig
from ..engine import Engine
from ..http import redact, register_secret
from ..models import Decision
from ..runtime import lan_addresses
from ..sources import RobloxUnavailable

log = logging.getLogger(__name__)
DASHBOARD = Path(__file__).with_name("dashboard.html")
STATIC = Path(__file__).with_name("static")


def _json(data, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str), status=status, content_type="application/json"
    )


# Panel yalnızca kendi dosyalarını ve https görsellerini yükler; başka kökene bağlanamaz, çerçeveye alınamaz.
CSP = (
    "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
MAX_SSE_CLIENTS = 20
WRITE_LIMIT, WRITE_WINDOW = 12, 60.0  # IP başına dakikada en fazla 12 yazma isteği (token denemeleri dahil)
VALID_DECISIONS = {d.value for d in Decision}


def is_loopback(addr: str | None) -> bool:
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def host_allowed(host_header: str, extra: list[str]) -> bool:
    """DNS rebinding savunması: Host başlığı yerel ad, IP adresi, *.local ya da izin verilen bir ad olmalı."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):  # [::1]:8080
        host = host[1 : host.find("]")] if "]" in host else host[1:]
    elif host.count(":") == 1:
        host = host.split(":")[0]
    if not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return any(fnmatch.fnmatchcase(host, pattern.lower()) for pattern in extra)


def create_app(engine: Engine, cfg: WebConfig) -> web.Application:
    writes: dict[str, list[float]] = {}
    busy = asyncio.Semaphore(2)  # aynı anda en fazla 2 Roblox sorgusu (kötüye kullanım ve hız sınırı)

    @web.middleware
    async def guard(request: web.Request, handler):
        if not host_allowed(request.headers.get("Host", ""), cfg.allowed_hosts):
            return _json({"error": "Bu adres izinli değil (web.allowed_hosts)"}, 403)
        return await handler(request)

    async def add_headers(_: web.Request, response: web.StreamResponse) -> None:
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)

    def write_denied(request: web.Request) -> web.Response | None:
        """Yazma/sorgu uçları: token varsa zorunlu; yoksa yalnızca bu cihazdan (loopback) serbest."""
        now = time.monotonic()
        ip = request.remote or "?"
        hits = [t for t in writes.get(ip, []) if now - t < WRITE_WINDOW]
        if len(hits) >= WRITE_LIMIT:
            return _json({"error": "Çok fazla istek; biraz bekle"}, 429)
        hits.append(now)
        writes[ip] = hits
        if len(writes) > 1000:  # bellek şişmesin
            for k in [k for k, v in writes.items() if not v or now - v[-1] > WRITE_WINDOW][:500]:
                writes.pop(k, None)
        if cfg.token:
            given = request.headers.get("Authorization", "").encode("utf-8", "ignore")
            if not hmac.compare_digest(given, f"Bearer {cfg.token}".encode("utf-8")):
                return _json({"error": "yetkisiz"}, 401)
            return None
        if not is_loopback(request.remote):
            return _json({"error": "Ağdan kontrol için .env içinde AKIM_WEB_TOKEN ayarla (yalnızca bu cihazdan serbest)"}, 403)
        return None

    async def read_body(request: web.Request) -> dict | web.Response:
        if request.content_type != "application/json":  # tarayıcıdan başka siteden gelen basit POST'lar elenir
            return _json({"error": "Content-Type application/json olmalı"}, 415)
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _json({"error": "Geçersiz JSON"}, 400)
        return body if isinstance(body, dict) else _json({"error": "JSON nesnesi bekleniyor"}, 400)

    def int_param(request: web.Request, name: str, default: int, hi: int) -> int:
        try:
            return max(1, min(hi, int(request.query.get(name, default))))
        except ValueError:
            raise web.HTTPBadRequest(text=json.dumps({"error": f"{name} sayı olmalı"}), content_type="application/json")

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
        decisions = [d for d in request.query.get("decision", "").split(",") if d in VALID_DECISIONS] or None
        return _json(engine.board(decisions, int_param(request, "limit", 200, 500)))

    async def game(request: web.Request) -> web.Response:
        detail = engine.game_detail(request.match_info["key"])
        return _json(detail) if detail else _json({"error": "bulunamadı"}, 404)

    async def upcoming(request: web.Request) -> web.Response:
        return _json(engine.upcoming_board(int_param(request, "limit", 50, 200)))

    async def alerts(request: web.Request) -> web.Response:
        return _json(engine.store.recent_alerts(int_param(request, "limit", 100, 500)))

    def failure(exc: Exception, what: str) -> web.Response:
        if isinstance(exc, RobloxUnavailable):
            return _json({"error": f"Roblox'a şu an ulaşılamıyor ({exc}). VPN gerekiyorsa aç ve tekrar dene."}, 503)
        log.warning("%s başarısız: %s", what, redact(str(exc)))
        return _json({"error": "İşlem şu an yapılamadı; biraz sonra tekrar dene."}, 502)

    async def check(request: web.Request) -> web.Response:
        if (denied := write_denied(request)) is not None:
            return denied
        body = await read_body(request)
        if isinstance(body, web.Response):
            return body
        title = str(body.get("title", "")).strip()[:200]
        if not title:
            return _json({"error": "title gerekli"}, 400)
        if busy.locked():
            return _json({"error": "Şu an başka sorgular çalışıyor; birkaç saniye sonra tekrar dene"}, 429)
        async with busy:
            try:
                return _json(await engine.adhoc_check(title))
            except Exception as exc:
                return failure(exc, "Anlık kontrol")

    async def rescan(request: web.Request) -> web.Response:
        if (denied := write_denied(request)) is not None:
            return denied
        key = request.match_info["key"]
        if not engine.store.get_game(key):
            return _json({"error": "bulunamadı"}, 404)
        if busy.locked():
            return _json({"error": "Şu an başka sorgular çalışıyor; birkaç saniye sonra tekrar dene"}, 429)
        async with busy:
            try:
                await engine.scan_roblox(key)
            except Exception as exc:
                return failure(exc, "Yeniden tarama")
        return _json(engine.game_detail(key))

    async def events(request: web.Request) -> web.StreamResponse:
        if engine.bus.subscriber_count >= MAX_SSE_CLIENTS:
            return _json({"error": "Çok fazla açık bağlantı"}, 503)
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

    @web.middleware
    async def api_no_store(request: web.Request, handler):
        resp = await handler(request)
        if request.path.startswith("/api/") and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(middlewares=[guard, api_no_store], client_max_size=16 * 1024)
    app.on_response_prepare.append(add_headers)
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
    register_secret(cfg.token)
    app = create_app(engine, cfg)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    exposed = not is_loopback(cfg.host) and cfg.host != "localhost"
    if exposed and not cfg.token:
        log.warning(
            "Panel ağdan erişilebilir ama AKIM_WEB_TOKEN boş: okuma serbest, kontrol/yeniden tarama yalnızca bu "
            "cihazdan çalışır. Ağdan kontrol için token ayarla; yalnızca bu cihaz için web.host: 127.0.0.1 yap."
        )
    elif exposed and len(cfg.token) < 12:
        log.warning("AKIM_WEB_TOKEN kısa (%d karakter); en az 16 karakterlik rastgele bir değer kullan.", len(cfg.token))
    if cfg.host in ("0.0.0.0", "", "::"):  # nosec B104 - yalnızca adres karşılaştırması, bağlama yapılmıyor
        log.info("Web paneli: http://localhost:%d", cfg.port)
        for ip in lan_addresses():
            log.info("Telefondan (aynı Wi-Fi): http://%s:%d", ip, cfg.port)
    else:
        log.info("Web paneli: http://%s:%d", cfg.host, cfg.port)
    return runner
