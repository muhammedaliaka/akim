"""Güvenlik: panel sıkılaştırması, gizli bilgi sızıntısı ve kanal enjeksiyonları."""

import asyncio
import base64
import json
import logging

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from akim.config import ChannelConfig, Config, WebConfig
from akim.http import HttpClient, RedactingFormatter, redact, register_secret
from akim.models import Alert, Priority
from akim.notify import build_channels
from akim.notify.base import html_text, oneline, plain_text, safe_url
from akim.notify.channels import DiscordChannel, EmailChannel, NtfyChannel, SlackChannel
from akim.runtime import lan_addresses
from akim.sources import RobloxUnavailable
from akim.web.server import MAX_SSE_CLIENTS, WRITE_LIMIT, create_app, host_allowed, is_loopback
from tests.test_engine import build

JSON = {"Content-Type": "application/json"}


def run(coro):
    return asyncio.run(coro)


async def client_for(web_cfg: WebConfig | None = None):
    engine, _ = build()
    app = create_app(engine, web_cfg or WebConfig())
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, client


# ---------------------------------------------------------------------- varsayılanlar
def test_panel_is_local_only_by_default():
    assert Config().web.host == "127.0.0.1" and Config().web.token == ""


# ---------------------------------------------------------------------- başlıklar ve Host doğrulaması
def test_security_headers_on_every_response():
    async def scenario():
        engine, client = await client_for()
        for path in ("/", "/api/status", "/static/icon.svg", "/yok-boyle-bir-yer", "/healthz"):
            resp = await client.get(path)
            h = resp.headers
            assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and "connect-src 'self'" in h["Content-Security-Policy"]
            assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
            assert h["Referrer-Policy"] == "no-referrer", path
        assert (await client.get("/api/status")).headers["Cache-Control"] == "no-store"
        await client.close()

    run(scenario())


@pytest.mark.parametrize(
    "host, ok",
    [
        ("127.0.0.1:8080", True), ("localhost:8080", True), ("[::1]:8080", True), ("192.168.1.20:8080", True),
        ("telefon.local", True), ("evil.example.com", False), ("evil.example.com:8080", False), ("", False),
        ("127.0.0.1.evil.com", False), ("localhost.evil.com", False),
    ],
)
def test_host_allowlist(host, ok):
    assert host_allowed(host, []) is ok


def test_host_allowlist_extra_names():
    assert host_allowed("pc.tailnet.ts.net:8080", ["*.ts.net"]) and not host_allowed("pc.evil.net", ["*.ts.net"])


def test_dns_rebinding_host_is_rejected():
    async def scenario():
        engine, client = await client_for()
        resp = await client.get("/api/status", headers={"Host": "evil.example.com"})
        assert resp.status == 403
        ok = await client.get("/api/status", headers={"Host": "localhost:8080"})
        assert ok.status == 200
        await client.close()
        engine, client = await client_for(WebConfig(allowed_hosts=["pc.tailnet.ts.net"]))
        assert (await client.get("/api/status", headers={"Host": "pc.tailnet.ts.net"})).status == 200
        await client.close()

    run(scenario())


# ---------------------------------------------------------------------- yazma uçları
def test_write_endpoints_validate_input():
    async def scenario():
        engine, client = await client_for()
        r = await client.post("/api/check", data='{"title": "x"}', headers={"Content-Type": "text/plain"})
        assert r.status == 415  # başka sitedeki formdan gelen "basit" POST engellenir
        assert (await client.post("/api/check", data="{bozuk", headers=JSON)).status == 400
        assert (await client.post("/api/check", data="[1,2]", headers=JSON)).status == 400
        assert (await client.post("/api/check", json={"title": "  "})).status == 400
        assert (await client.post("/api/check", data=json.dumps({"title": "x" * 40_000}), headers=JSON)).status == 413
        await client.close()

    run(scenario())


def test_writes_are_rate_limited():
    async def scenario():
        engine, client = await client_for()
        codes = [(await client.post("/api/check", json={"title": ""})).status for _ in range(WRITE_LIMIT + 3)]
        assert codes[:WRITE_LIMIT] == [400] * WRITE_LIMIT and codes[WRITE_LIMIT:] == [429] * 3
        await client.close()

    run(scenario())


def test_token_is_enforced_and_odd_headers_do_not_crash():
    async def scenario():
        engine, client = await client_for(WebConfig(token="gizli-token-1234567890"))
        assert (await client.post("/api/check", json={"title": "x"})).status == 401
        assert (await client.post("/api/check", json={"title": "x"}, headers={"Authorization": "Bearer yanlis"})).status == 401
        # ASCII olmayan başlık eskiden TypeError ile 500 üretebilirdi
        assert (await client.post("/api/check", json={"title": "x"}, headers={"Authorization": "Bearer şğü".encode("utf-8").decode("latin-1")})).status == 401
        good = {"Authorization": "Bearer gizli-token-1234567890"}
        assert (await client.post("/api/check", json={"title": ""}, headers=good)).status == 400  # yetkili: giriş doğrulamasına geçer
        await client.close()

    run(scenario())


@pytest.mark.skipif(not lan_addresses(), reason="bu makinede loopback dışı bir adres yok")
def test_network_clients_cannot_control_without_token_but_can_read():
    async def scenario():
        engine, _ = build()
        app = create_app(engine, WebConfig(host="0.0.0.0"))
        server = TestServer(app, host="0.0.0.0")
        await server.start_server()
        ip = lan_addresses()[0]
        base = f"http://{ip}:{server.port}"
        async with aiohttp.ClientSession() as s:
            assert (await s.get(f"{base}/api/status")).status == 200  # okuma serbest
            r = await s.post(f"{base}/api/check", json={"title": "Peak"})
            assert r.status == 403 and "AKIM_WEB_TOKEN" in (await r.json())["error"]
            assert (await s.post(f"{base}/api/rescan/steam:1")).status == 403
        # aynı cihazdan (loopback) token'sız kontrol çalışır
        async with aiohttp.ClientSession() as s:
            r = await s.post(f"http://127.0.0.1:{server.port}/api/check", json={"title": ""})
            assert r.status == 400
        await server.close()

    run(scenario())


def test_loopback_detection():
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("::ffff:127.0.0.1") and is_loopback("127.5.5.5")
    assert not is_loopback("192.168.1.2") and not is_loopback("0.0.0.0") and not is_loopback(None) and not is_loopback("localhost.evil")


def test_query_params_are_validated():
    async def scenario():
        engine, client = await client_for()
        assert (await client.get("/api/board?limit=abc")).status == 400
        assert (await client.get("/api/alerts?limit=-5")).status == 200
        assert (await client.get("/api/board?decision=opportunity,'%20OR%201=1--")).status == 200  # bilinmeyenler yok sayılır
        await client.close()

    run(scenario())


def test_event_stream_connections_are_capped():
    async def scenario():
        engine, client = await client_for()
        subs = [engine.bus.subscribe() for _ in range(MAX_SSE_CLIENTS)]
        assert (await client.get("/api/events")).status == 503
        await client.close()

    run(scenario())


def test_errors_never_leak_internals_or_secrets():
    async def scenario():
        engine, client = await client_for()

        async def boom(title):
            raise RuntimeError("bağlantı https://api.telegram.org/bot123456:SECRETTOKEN/sendMessage koptu")

        engine.adhoc_check = boom
        r = await client.post("/api/check", json={"title": "x"})
        body = await r.text()
        assert r.status == 502 and "SECRETTOKEN" not in body and "RuntimeError" not in body

        async def vpn(title):
            raise RobloxUnavailable("adres çözülemedi (internet yok ya da site engelli)", "ham ayrıntı")

        engine.adhoc_check = vpn
        r = await client.post("/api/check", json={"title": "x"})
        assert r.status == 503 and "VPN" in (await r.json())["error"] and "ham ayrıntı" not in await r.text()
        await client.close()

    run(scenario())


# ---------------------------------------------------------------------- gizli bilgi maskeleme
def test_registered_secrets_are_masked_everywhere():
    register_secret("akim-ozel-konu-9f3a")
    assert redact("POST https://ntfy.sh/akim-ozel-konu-9f3a başarısız") == "POST https://ntfy.sh/*** başarısız"
    assert redact("https://api.telegram.org/bot123:ABC/sendMessage") == "https://api.telegram.org/bot***/sendMessage"
    fmt = RedactingFormatter("%(message)s")
    try:
        raise ValueError("konu akim-ozel-konu-9f3a sızdı")
    except ValueError:
        import sys

        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "hata", None, sys.exc_info())
    assert "akim-ozel-konu-9f3a" not in fmt.format(rec)  # istisna izi de maskelenir


def test_network_errors_do_not_expose_topic_or_token(monkeypatch):
    monkeypatch.setattr("akim.http._backoff", lambda attempt: 0.0)  # yeniden denemeler beklemesin

    async def scenario():
        register_secret("akim-ozel-konu-9f3a")
        http = HttpClient()
        with pytest.raises(ConnectionError) as info:
            await http.get_text("http://127.0.0.1:1/akim-ozel-konu-9f3a", retries=0)
        assert "akim-ozel-konu-9f3a" not in str(info.value)
        ch = NtfyChannel(ChannelConfig(enabled=True, server="http://127.0.0.1:1", topic="akim-ozel-konu-9f3a"), http)
        assert await ch.safe_send(Alert(type="x", priority=Priority.HIGH, title="t", body="b")) is False
        assert "akim-ozel-konu-9f3a" not in (ch.last_error or "")  # panelde (/api/status) görünen metin
        await http.close()

    run(scenario())


def test_build_channels_registers_secrets():
    cfg = Config()
    cfg.notifications.channels["ntfy"] = ChannelConfig(enabled=True, topic="kayitli-gizli-konu-77")
    build_channels(cfg.notifications, HttpClient(), None)
    assert "kayitli-gizli-konu-77" not in redact("x kayitli-gizli-konu-77 y")


# ---------------------------------------------------------------------- kanal enjeksiyonları
EVIL = "Moss <!channel> <@U123> @everyone\r\nX-Evil: 1"


def test_oneline_and_safe_url():
    assert oneline("a\r\nb\tc\x00d") == "a b c d"
    assert safe_url("https://store.steampowered.com/app/1") and safe_url("javascript:alert(1)") is None
    assert safe_url("https://x.example/a b") is None and safe_url("https://x.example/\r\nEvil: 1") is None
    a = Alert(type="x", priority=Priority.LOW, title="t", body="b", url="javascript:alert(1)")
    assert "javascript" not in plain_text(a) and "javascript" not in html_text(a)


def _capture(paths):
    got = {}

    async def make():
        app = web.Application()
        for p in paths:
            async def handler(req, p=p):
                got[p] = (dict(req.headers), await req.read())
                return web.json_response({})
            app.router.add_post(p, handler)
        server = TestServer(app)
        await server.start_server()
        return server

    return got, make


def test_slack_discord_ntfy_email_are_safe_against_hostile_titles():
    async def scenario():
        got, make = _capture(["/slack", "/discord", "/konu"])
        server = await make()
        http = HttpClient()
        base = str(server.make_url("/"))
        a = Alert(type="x", priority=Priority.HIGH, title=EVIL, body="gövde <!here>", url="https://a.example/x",
                  fields=[("Roblox", "<!channel>")])
        await SlackChannel(ChannelConfig(enabled=True, webhook_url=base + "slack"), http).send(a)
        await DiscordChannel(ChannelConfig(enabled=True, webhook_url=base + "discord"), http).send(a)
        await NtfyChannel(ChannelConfig(enabled=True, server=base.rstrip("/"), topic="konu"), http).send(a)

        slack = json.loads(got["/slack"][1])["text"]
        assert "<!channel>" not in slack and "<!here>" not in slack and "<@U123>" not in slack and "&lt;!channel&gt;" in slack
        assert json.loads(got["/discord"][1])["allowed_mentions"] == {"parse": []}
        title = got["/konu"][0]["Title"]
        if title.startswith("=?UTF-8?B?"):
            title = base64.b64decode(title[10:-2]).decode()
        assert "\n" not in title and "\r" not in title and "X-Evil" in title  # tek satıra indirildi, ayrı başlık olmadı
        assert "X-Evil" not in got["/konu"][0]
        await http.close()
        await server.close()

    run(scenario())


def test_email_subject_cannot_inject_headers():
    ch = EmailChannel(ChannelConfig(enabled=True, smtp_host="x", recipients=["a@example.com"], sender="s@example.com"))
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    import akim.notify.channels as mod

    original = mod.smtplib.SMTP
    mod.smtplib.SMTP = FakeSMTP
    try:
        ch._send_sync(Alert(type="x", priority=Priority.HIGH, title=EVIL, body="b"))
    finally:
        mod.smtplib.SMTP = original
    msg = sent[0]
    assert "\n" not in msg["Subject"] and msg.get("X-Evil") is None


def test_telegram_errors_are_generic():
    from akim.notify.telegram import TelegramChannel

    async def scenario():
        replies = []

        async def bad(cmd, arg):
            raise RuntimeError("SECRETTOKEN iç hata <b>")

        ch = TelegramChannel(ChannelConfig(enabled=True, bot_token="123456:ABCDEF", chat_id="1"), HttpClient(), bad)

        async def fake_send(text, chat_id=None, preview=False):
            replies.append(text)

        ch.send_html = fake_send
        await ch._handle("1", "kontrol", "x")
        assert replies and "SECRETTOKEN" not in replies[0] and "RuntimeError" not in replies[0]

    run(scenario())
