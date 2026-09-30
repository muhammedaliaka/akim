"""Bildirim kanallarının gerçek HTTP isteklerini sahte bir sunucuya karşı doğrular."""

import asyncio
import base64

from aiohttp import web
from aiohttp.test_utils import TestServer

from akim.config import ChannelConfig
from akim.http import HttpClient
from akim.models import Alert, Priority
from akim.notify.channels import DiscordChannel, NtfyChannel, SlackChannel
from akim.notify.telegram import TelegramChannel


def make_alert():
    return Alert(
        type="opportunity", priority=Priority.CRITICAL, title="FIRSAT: Moss <Diver>",
        body="Roblox'ta henüz karşılığı YOK.", url="https://store.example/app/1",
        image="https://img.example/1.jpg", fields=[("Skor", "0.81")],
    )


async def fake_server(handlers):
    app = web.Application()
    for path, fn in handlers.items():
        app.router.add_post(path, fn)
    server = TestServer(app)
    await server.start_server()
    return server


def test_telegram_send_and_commands():
    async def scenario():
        sent, updates_served = [], {"n": 0}

        async def send_message(req):
            sent.append(await req.json())
            return web.json_response({"ok": True, "result": {}})

        async def get_updates(req):
            updates_served["n"] += 1
            if updates_served["n"] == 1:
                return web.json_response({"ok": True, "result": [
                    {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/durum"}},  # yetkisiz
                    {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/kontrol@AkimBot Schedule I"}},
                    {"update_id": 3, "message": {"chat": {"id": 42}, "text": "merhaba"}},  # komut değil
                ]})
            await asyncio.sleep(0.2)
            return web.json_response({"ok": True, "result": []})

        async def ok(req):
            return web.json_response({"ok": True, "result": True})

        server = await fake_server({
            "/botT/sendMessage": send_message, "/botT/getUpdates": get_updates, "/botT/setMyCommands": ok,
        })
        calls = []

        async def handler(cmd, arg):
            calls.append((cmd, arg))
            return f"<b>{cmd}</b> {arg}"

        http = HttpClient()
        ch = TelegramChannel(ChannelConfig(enabled=True, bot_token="T", chat_id="42"), http, handler)
        ch.api = str(server.make_url("/botT"))
        await ch.send(make_alert())
        assert "FIRSAT: Moss &lt;Diver&gt;" in sent[0]["text"]  # HTML kaçışı
        assert sent[0]["parse_mode"] == "HTML" and sent[0]["chat_id"] == "42"

        await ch.start()
        for _ in range(50):
            if len(sent) >= 2:
                break
            await asyncio.sleep(0.05)
        await ch.stop()
        assert calls == [("kontrol", "Schedule I")], "yalnızca yetkili sohbetin komutu işlenmeli"
        assert sent[1]["text"].startswith("<b>kontrol</b>") and sent[1]["chat_id"] == "42"
        await http.close()
        await server.close()

    asyncio.run(scenario())


def test_ntfy_discord_slack_payloads():
    async def scenario():
        got = {}

        async def ntfy(req):
            got["ntfy"] = (dict(req.headers), await req.text())
            return web.json_response({})

        async def discord(req):
            got["discord"] = await req.json()
            return web.json_response({})

        async def slack(req):
            got["slack"] = await req.json()
            return web.json_response({})

        server = await fake_server({"/konu": ntfy, "/discord": discord, "/slack": slack})
        http = HttpClient()
        base = str(server.make_url(""))
        a = make_alert()
        await NtfyChannel(ChannelConfig(enabled=True, server=base, topic="konu"), http).send(a)
        await DiscordChannel(ChannelConfig(enabled=True, webhook_url=base + "/discord"), http).send(a)
        await SlackChannel(ChannelConfig(enabled=True, webhook_url=base + "/slack"), http).send(a)

        headers, body = got["ntfy"]
        assert headers["Priority"] == "5" and headers["Click"] == a.url and headers["Attach"] == a.image
        # Türkçe başlık latin-1 dışı karakter içermediği sürece düz gider, içeriyorsa RFC 2047 ile
        title = headers["Title"]
        if title.startswith("=?UTF-8?B?"):
            title = base64.b64decode(title[10:-2]).decode()
        assert title == a.title
        assert "Skor: 0.81" in body
        embed = got["discord"]["embeds"][0]
        assert embed["url"] == a.url and embed["fields"][0] == {"name": "Skor", "value": "0.81", "inline": True}
        assert "*FIRSAT: Moss <Diver>*" in got["slack"]["text"]
        await http.close()
        await server.close()

    asyncio.run(scenario())


def test_ntfy_encodes_turkish_title():
    from akim.notify.channels import _rfc2047

    enc = _rfc2047("Akım başladı: Çığ")
    assert enc.startswith("=?UTF-8?B?")
    assert base64.b64decode(enc[10:-2]).decode() == "Akım başladı: Çığ"
    assert _rfc2047("FIRSAT: Peak") == "FIRSAT: Peak"
