"""Bildirim dağıtıcısı: tekrar koruması, kalıcı kayıt, canlı yayın ve kanallara gönderim."""

from __future__ import annotations

import asyncio
import logging
import time

from ..config import NotificationConfig
from ..events import EventBus
from ..http import HttpClient
from ..models import Alert, Priority
from ..storage import Storage
from .base import Channel
from .channels import ConsoleChannel, DiscordChannel, EmailChannel, NtfyChannel, SlackChannel, WebhookChannel
from .telegram import CommandHandler, TelegramChannel

log = logging.getLogger(__name__)


def build_channels(cfg: NotificationConfig, http: HttpClient, command_handler: CommandHandler | None) -> list[Channel]:
    channels: list[Channel] = []
    for name, ch in cfg.channels.items():
        if not ch.enabled:
            continue
        kind = name.split(":")[0]  # "discord:ekip" gibi aynı türden birden fazla kanal
        try:
            if kind == "console":
                channels.append(ConsoleChannel(ch))
            elif kind == "telegram":
                channels.append(TelegramChannel(ch, http, command_handler))
            elif kind == "discord":
                channels.append(DiscordChannel(ch, http))
            elif kind == "slack":
                channels.append(SlackChannel(ch, http))
            elif kind == "ntfy":
                channels.append(NtfyChannel(ch, http))
            elif kind == "webhook":
                channels.append(WebhookChannel(ch, http))
            elif kind == "email":
                channels.append(EmailChannel(ch))
            else:
                log.error("Bilinmeyen bildirim kanalı: %s", name)
                continue
            channels[-1].name = name
        except ValueError as exc:
            log.error("Kanal '%s' devre dışı: %s", name, exc)
    return channels


class Notifier:
    def __init__(self, cfg: NotificationConfig, store: Storage, bus: EventBus, channels: list[Channel]):
        self.cfg = cfg
        self.store = store
        self.bus = bus
        self.channels = channels

    async def start(self) -> None:
        for ch in self.channels:
            await ch.start()
        log.info("Bildirim kanalları: %s", ", ".join(f"{c.name}(≥{c.min_priority.name.lower()})" for c in self.channels) or "yok")

    async def stop(self) -> None:
        for ch in self.channels:
            await ch.stop()

    def on_cooldown(self, alert: Alert, cooldown_hours: float | None = None) -> bool:
        key = alert.dedupe_key or alert.game_key
        if not key:
            return False
        hours = self.cfg.cooldown_hours if cooldown_hours is None else cooldown_hours
        last = self.store.alert_last_sent(key, alert.type)
        return last is not None and time.time() - last < hours * 3600

    async def dispatch(self, alert: Alert, *, cooldown_hours: float | None = None, force: bool = False) -> bool:
        """Bildirimi kaydeder ve uygun kanallara gönderir. Tekrar korumasına takılırsa False döner."""
        if not force and self.on_cooldown(alert, cooldown_hours):
            log.debug("Tekrar koruması: %s / %s", alert.game_key, alert.type)
            return False
        alert.id = self.store.add_alert(alert)
        if alert.dedupe_key or alert.game_key:
            self.store.mark_alert_sent(alert.dedupe_key or alert.game_key, alert.type, alert.ts)
        self.bus.publish("alert", alert.to_dict())

        targets = [c for c in self.channels if c.accepts(alert)]
        if targets:
            await asyncio.gather(*(c.safe_send(alert) for c in targets))
        return True

    async def test(self) -> dict[str, bool]:
        alert = Alert(
            type="test",
            priority=Priority.CRITICAL,
            title="Akım test bildirimi",
            body="Bu kanal doğru yapılandırılmış. Fırsatlar buraya anlık olarak düşecek.",
            fields=[("Durum", "Çalışıyor ✅")],
        )
        results = await asyncio.gather(*(c.safe_send(alert) for c in self.channels))
        return {c.name: ok for c, ok in zip(self.channels, results)}

    def channel_health(self) -> list[dict]:
        return [
            {"name": c.name, "min_priority": c.min_priority.name.lower(), "last_ok": c.last_ok, "last_error": c.last_error}
            for c in self.channels
        ]
