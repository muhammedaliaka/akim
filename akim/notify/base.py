from __future__ import annotations

import abc
import html
import logging
import time

from ..config import ChannelConfig
from ..models import Alert, Priority

log = logging.getLogger(__name__)

PRIORITY_EMOJI = {
    Priority.LOW: "ℹ️",
    Priority.MEDIUM: "🔔",
    Priority.HIGH: "🚨",
    Priority.CRITICAL: "🔥",
}
PRIORITY_LABEL = {
    Priority.LOW: "Bilgi",
    Priority.MEDIUM: "Orta",
    Priority.HIGH: "Yüksek",
    Priority.CRITICAL: "Kritik",
}


class Channel(abc.ABC):
    name = "channel"

    def __init__(self, cfg: ChannelConfig):
        self.cfg = cfg
        self.min_priority = Priority.parse(cfg.min_priority)
        self.last_error: str | None = None
        self.last_ok: float | None = None

    def accepts(self, alert: Alert) -> bool:
        return alert.priority >= self.min_priority

    @abc.abstractmethod
    async def send(self, alert: Alert) -> None: ...

    async def safe_send(self, alert: Alert) -> bool:
        try:
            await self.send(alert)
            self.last_ok = time.time()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("%s kanalına gönderilemedi: %s", self.name, exc)
            return False

    async def start(self) -> None:
        """Arka plan görevi gerektiren kanallar (ör. Telegram komutları) için."""

    async def stop(self) -> None: ...


def plain_text(alert: Alert, *, with_fields: bool = True) -> str:
    lines = [f"{PRIORITY_EMOJI[alert.priority]} {alert.title}", "", alert.body]
    if with_fields and alert.fields:
        lines.append("")
        lines += [f"• {k}: {v}" for k, v in alert.fields]
    if alert.url:
        lines += ["", alert.url]
    return "\n".join(lines).strip()


def html_text(alert: Alert) -> str:
    e = html.escape
    lines = [f"{PRIORITY_EMOJI[alert.priority]} <b>{e(alert.title)}</b>", "", e(alert.body)]
    if alert.fields:
        lines.append("")
        lines += [f"• <b>{e(k)}:</b> {e(v)}" for k, v in alert.fields]
    if alert.url:
        lines += ["", f'<a href="{e(alert.url, quote=True)}">Mağaza sayfası</a>']
    return "\n".join(lines).strip()
