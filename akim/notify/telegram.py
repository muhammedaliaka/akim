"""Telegram kanalı: bildirim gönderir ve isteğe bağlı olarak sohbet komutlarını dinler.

Komutlar (yalnızca yapılandırılmış chat_id'den kabul edilir):
  /firsatlar          güncel fırsat listesi
  /yaklasan           fragmandan yakalanan, henüz çıkmamış oyunlar
  /rapor [gün]        son günlerin sade raporu
  /durum              sistem ve kaynak sağlığı
  /kontrol <oyun>     herhangi bir oyunu anında Roblox'ta kontrol et
  /oyun <oyun>        takip edilen bir oyunun detayı
  /yardim             komut listesi
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from ..config import ChannelConfig
from ..http import HttpClient, HttpError
from ..models import Alert
from .base import Channel, html_text

log = logging.getLogger(__name__)

CommandHandler = Callable[[str, str], Awaitable[str]]


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, cfg: ChannelConfig, http: HttpClient, command_handler: CommandHandler | None = None):
        super().__init__(cfg)
        if not cfg.bot_token or not cfg.chat_id:
            raise ValueError("telegram.bot_token ve telegram.chat_id gerekli")
        self.http = http
        self.api = f"https://api.telegram.org/bot{cfg.bot_token}"
        self.command_handler = command_handler
        self._task: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()  # asyncio görevleri zayıf referansla tutar

    async def _call(self, method: str, payload: dict) -> dict:
        _, text = await self.http.post(f"{self.api}/{method}", json_body=payload, retries=2)
        return json.loads(text)

    async def send_html(self, text: str, chat_id: str | None = None, preview: bool = False) -> None:
        # Telegram mesaj sınırı 4096 karakter
        for chunk in _chunks(text, 4000):
            await self._call(
                "sendMessage",
                {
                    "chat_id": chat_id or self.cfg.chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": not preview,
                },
            )

    async def send(self, alert: Alert) -> None:
        await self.send_html(html_text(alert), preview=bool(alert.url))

    # ------------------------------------------------------------------ komutlar
    async def start(self) -> None:
        if self.cfg.commands and self.command_handler and self._task is None:
            self._task = asyncio.create_task(self._poll(), name="telegram-commands")
            try:
                await self._call(
                    "setMyCommands",
                    {
                        "commands": [
                            {"command": "firsatlar", "description": "Güncel fırsatlar"},
                            {"command": "kontrol", "description": "Bir oyunu Roblox'ta kontrol et"},
                            {"command": "oyun", "description": "Takip edilen oyunun detayı"},
                            {"command": "yaklasan", "description": "Fragmandan yakalanan çıkmamış oyunlar"},
                            {"command": "rapor", "description": "Son günlerin raporu"},
                            {"command": "durum", "description": "Sistem durumu"},
                            {"command": "yardim", "description": "Yardım"},
                        ]
                    },
                )
            except Exception as exc:
                log.debug("setMyCommands başarısız: %s", exc)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _poll(self) -> None:
        offset = None
        while True:
            try:
                payload = {"timeout": 50, "allowed_updates": ["message"]}
                if offset is not None:
                    payload["offset"] = offset
                _, text = await self.http.post(
                    f"{self.api}/getUpdates", json_body=payload, retries=0, timeout=65
                )
                data = json.loads(text)
                for upd in data.get("result") or []:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    chat_id = str((msg.get("chat") or {}).get("id", ""))
                    body = (msg.get("text") or "").strip()
                    if not body.startswith("/"):
                        continue
                    if chat_id != str(self.cfg.chat_id):
                        log.warning("Yetkisiz Telegram sohbetinden komut yok sayıldı: %s", chat_id)
                        continue
                    cmd, _, arg = body[1:].partition(" ")
                    cmd = cmd.split("@")[0].lower()
                    task = asyncio.create_task(self._handle(chat_id, cmd, arg.strip()))
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight.discard)
            except asyncio.CancelledError:
                raise
            except HttpError as exc:
                if exc.status == 409:  # başka bir getUpdates/webhook çalışıyor
                    log.error("Telegram komutları başka bir süreç tarafından dinleniyor (409); 60 sn bekleniyor")
                    await asyncio.sleep(60)
                else:
                    log.warning("Telegram getUpdates hatası: %s", exc)
                    await asyncio.sleep(10)
            except Exception as exc:
                log.warning("Telegram getUpdates hatası: %s", exc)
                await asyncio.sleep(10)

    async def _handle(self, chat_id: str, cmd: str, arg: str) -> None:
        try:
            reply = await self.command_handler(cmd, arg)  # type: ignore[misc]
        except Exception as exc:
            log.exception("Komut işlenemedi: /%s", cmd)
            reply = "⚠️ Komut şu an işlenemedi; biraz sonra tekrar dene."
        try:
            await self.send_html(reply, chat_id=chat_id)
        except Exception as exc:
            log.warning("Telegram yanıtı gönderilemedi: %s", exc)


def _chunks(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > size:
            out.append(buf)
            buf = ""
        buf += line + "\n"
    if buf:
        out.append(buf)
    return out
