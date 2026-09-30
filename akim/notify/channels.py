"""Bildirim kanalları: konsol, Discord, Slack, ntfy (telefon push), genel webhook, e-posta."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..config import ChannelConfig
from ..http import HttpClient
from ..models import Alert, Priority
from .base import PRIORITY_EMOJI, PRIORITY_LABEL, Channel, plain_text

log = logging.getLogger("akim.alert")

_ANSI = {
    Priority.LOW: "\033[37m",
    Priority.MEDIUM: "\033[36m",
    Priority.HIGH: "\033[33;1m",
    Priority.CRITICAL: "\033[31;1m",
}


class ConsoleChannel(Channel):
    name = "console"

    async def send(self, alert: Alert) -> None:
        color, reset = _ANSI[alert.priority], "\033[0m"
        text = plain_text(alert).replace("\n", "\n    ")
        log.info("%s%s%s", color, text, reset)


class DiscordChannel(Channel):
    name = "discord"
    COLORS = {Priority.LOW: 0x95A5A6, Priority.MEDIUM: 0x3498DB, Priority.HIGH: 0xF39C12, Priority.CRITICAL: 0xE74C3C}

    def __init__(self, cfg: ChannelConfig, http: HttpClient):
        super().__init__(cfg)
        if not cfg.webhook_url:
            raise ValueError("discord.webhook_url gerekli")
        self.http = http

    async def send(self, alert: Alert) -> None:
        embed = {
            "title": f"{PRIORITY_EMOJI[alert.priority]} {alert.title}"[:256],
            "description": alert.body[:4000],
            "color": self.COLORS[alert.priority],
            "fields": [{"name": k[:256], "value": (v or "-")[:1024], "inline": True} for k, v in alert.fields[:25]],
            "footer": {"text": f"Akım • {PRIORITY_LABEL[alert.priority]} öncelik"},
        }
        if alert.url:
            embed["url"] = alert.url
        if alert.image:
            embed["thumbnail"] = {"url": alert.image}
        await self.http.post(self.cfg.webhook_url, json_body={"username": "Akım", "embeds": [embed]}, retries=2)


class SlackChannel(Channel):
    name = "slack"

    def __init__(self, cfg: ChannelConfig, http: HttpClient):
        super().__init__(cfg)
        if not cfg.webhook_url:
            raise ValueError("slack.webhook_url gerekli")
        self.http = http

    async def send(self, alert: Alert) -> None:
        fields = "\n".join(f"*{k}:* {v}" for k, v in alert.fields)
        link = f"\n<{alert.url}|Mağaza sayfası>" if alert.url else ""
        text = f"{PRIORITY_EMOJI[alert.priority]} *{alert.title}*\n{alert.body}\n{fields}{link}"
        await self.http.post(self.cfg.webhook_url, json_body={"text": text}, retries=2)


class NtfyChannel(Channel):
    """ntfy.sh: telefona anında push bildirimi (uygulamayı kurup konuya abone olmak yeterli)."""

    name = "ntfy"
    PRIO = {Priority.LOW: "2", Priority.MEDIUM: "3", Priority.HIGH: "4", Priority.CRITICAL: "5"}
    TAGS = {Priority.LOW: "information_source", Priority.MEDIUM: "bell", Priority.HIGH: "rotating_light", Priority.CRITICAL: "fire"}

    def __init__(self, cfg: ChannelConfig, http: HttpClient):
        super().__init__(cfg)
        if not cfg.topic:
            raise ValueError("ntfy.topic gerekli")
        self.http = http

    async def send(self, alert: Alert) -> None:
        body = alert.body
        if alert.fields:
            body += "\n" + "\n".join(f"{k}: {v}" for k, v in alert.fields)
        headers = {
            # HTTP başlıkları latin-1 olmalı; Türkçe başlıklar için RFC 2047 kodlaması
            "Title": _rfc2047(alert.title),
            "Priority": self.PRIO[alert.priority],
            "Tags": self.TAGS[alert.priority],
        }
        if alert.url:
            headers["Click"] = alert.url
        if alert.image:
            headers["Attach"] = alert.image
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        url = f"{self.cfg.server.rstrip('/')}/{self.cfg.topic}"
        await self.http.post(url, data=body.encode("utf-8"), headers=headers, retries=2)


def _rfc2047(text: str) -> str:
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        import base64

        return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode() + "?="


class WebhookChannel(Channel):
    """Herhangi bir sisteme (n8n, Zapier, kendi sunucun) JSON POST."""

    name = "webhook"

    def __init__(self, cfg: ChannelConfig, http: HttpClient):
        super().__init__(cfg)
        if not cfg.url:
            raise ValueError("webhook.url gerekli")
        self.http = http

    async def send(self, alert: Alert) -> None:
        await self.http.post(self.cfg.url, json_body=alert.to_dict(), headers=self.cfg.headers or None, retries=2)


class EmailChannel(Channel):
    name = "email"

    def __init__(self, cfg: ChannelConfig):
        super().__init__(cfg)
        if not (cfg.smtp_host and cfg.recipients and (cfg.sender or cfg.username)):
            raise ValueError("email: smtp_host, sender ve recipients gerekli")

    def _send_sync(self, alert: Alert) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[Akım] {alert.title}"
        msg["From"] = self.cfg.sender or self.cfg.username
        msg["To"] = ", ".join(self.cfg.recipients)
        msg.set_content(plain_text(alert))
        ctx = ssl.create_default_context()
        if self.cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, context=ctx, timeout=30) as s:
                if self.cfg.username:
                    s.login(self.cfg.username, self.cfg.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as s:
                if self.cfg.starttls:
                    s.starttls(context=ctx)
                if self.cfg.username:
                    s.login(self.cfg.username, self.cfg.password)
                s.send_message(msg)

    async def send(self, alert: Alert) -> None:
        await asyncio.to_thread(self._send_sync, alert)
