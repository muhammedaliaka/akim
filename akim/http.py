"""Yeniden deneme, geri çekilme ve host başına hız sınırı olan ortak HTTP istemcisi."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 akim-trend-monitor/0.1"
)
RETRY_STATUSES = {429, 500, 502, 503, 504}


_SECRET_PATTERNS = [
    (re.compile(r"/bot[^/]+/"), "/bot***/"),  # Telegram bot token
    (re.compile(r"(/webhooks/\d+/)[^/?]+"), r"\1***"),  # Discord webhook token
    (re.compile(r"(hooks\.slack\.com/services/)[^?]+"), r"\1***"),
]


# Yapılandırmadan gelen gizli değerler (bot token, webhook adresi, ntfy konusu, parolalar...). ntfy konusu da bir
# parola gibidir: bilen herkes bildirimleri okuyabilir. Her metinde bunlar maskelenir.
_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Bu değer bundan sonra log, hata ve panel metinlerinde '***' olarak görünür (çok kısa değerler yok sayılır)."""
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    """Log ve hata mesajlarında gizli bilgi taşıyan parçaları maskeler."""
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    for secret in _SECRETS:
        if secret in text:
            text = text.replace(secret, "***")
    return text


class RedactingFormatter(logging.Formatter):
    """Biçimlenmiş log satırını (istisna izleri dahil) yazmadan önce gizli bilgilerden arındırır."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} {redact(url)} {body[:200]}")
        self.status = status
        self.url = redact(url)


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 25,
        max_retries: int = 4,
        user_agent: str = DEFAULT_UA,
        host_min_interval: dict[str, float] | None = None,
    ):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._ua = user_agent
        self._session: aiohttp.ClientSession | None = None
        self._host_min_interval = host_min_interval or {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        # 429 alan host için bekleme aralığı geçici olarak büyütülür, başarıyla yavaşça küçülür
        self._host_penalty: dict[str, float] = {}

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def set_host_interval(self, host: str, seconds: float) -> None:
        self._host_min_interval[host] = seconds

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # trust_env: HTTPS_PROXY / SSL_CERT_FILE gibi ortam ayarlarına uy
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": self._ua, "Accept-Language": "en-US,en;q=0.9"},
                trust_env=True,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _on_rate_limited(self, host: str) -> None:
        base = self._host_min_interval.get(host) or 0.5
        cur = self._host_penalty.get(host, base)
        self._host_penalty[host] = min(15.0, max(base, cur) * 2)

    def _on_success(self, host: str) -> None:
        if host in self._host_penalty:
            base = self._host_min_interval.get(host) or 0.0
            cur = self._host_penalty[host] * 0.85
            if cur <= max(base, 0.05) * 1.05:
                del self._host_penalty[host]
            else:
                self._host_penalty[host] = cur

    def current_interval(self, host: str) -> float:
        return max(self._host_min_interval.get(host) or 0.0, self._host_penalty.get(host, 0.0))

    async def _throttle(self, host: str) -> None:
        interval = self.current_interval(host)
        if not interval:
            return
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        data: Any = None,
        headers: dict | None = None,
        retries: int | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str]:
        host = urlsplit(url).hostname or ""
        attempts = (self._max_retries if retries is None else retries) + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            await self._throttle(host)
            try:
                session = await self._get_session()
                extra = {"timeout": aiohttp.ClientTimeout(total=timeout)} if timeout else {}
                async with session.request(
                    method, url, params=params, json=json_body, data=data, headers=headers, **extra
                ) as resp:
                    text = await resp.text(errors="replace")
                    if resp.status == 429:
                        self._on_rate_limited(host)
                    elif resp.status < 400:
                        self._on_success(host)
                    if resp.status in RETRY_STATUSES and attempt < attempts - 1:
                        delay = _retry_after(resp.headers.get("Retry-After")) or _backoff(attempt)
                        log.warning("%s %s -> %s, %.1fs sonra tekrar", method, redact(url), resp.status, delay)
                        await asyncio.sleep(delay)
                        continue
                    if resp.status >= 400:
                        raise HttpError(resp.status, url, text)
                    return resp.status, text
            except HttpError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    delay = _backoff(attempt)
                    log.warning("%s %s ağ hatası (%s), %.1fs sonra tekrar", method, redact(url), redact(str(exc)), delay)
                    await asyncio.sleep(delay)
                    continue
        raise ConnectionError(redact(f"{method} {url} başarısız: {last_exc}"))

    async def get_json(self, url: str, **kw) -> Any:
        _, text = await self.request("GET", url, **kw)
        return json.loads(text)

    async def get_text(self, url: str, **kw) -> str:
        _, text = await self.request("GET", url, **kw)
        return text

    async def post(self, url: str, **kw) -> tuple[int, str]:
        return await self.request("POST", url, **kw)


def _backoff(attempt: int) -> float:
    return min(60.0, 2.0 * (2**attempt)) + random.uniform(0, 1)  # nosec B311 - yeniden deneme titreşimi, güvenlik amaçlı değil


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return min(120.0, max(0.0, float(value)))
    except ValueError:
        return None
