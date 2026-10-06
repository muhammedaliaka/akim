"""YouTube kanal akışları (RSS): API anahtarı, hesap ve ücret gerektirmez.

Her kanalın son ~15 videosu başlık, yayın zamanı ve görüntülenme sayısıyla gelir. Kanal ``UC...`` kimliğiyle ya da
``@tanıtıcı`` ile verilebilir; tanıtıcı kanal sayfasından çözülür ve önbelleğe alınır.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from ..http import HttpClient

log = logging.getLogger(__name__)

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "m": "http://search.yahoo.com/mrss/",
}
_CANONICAL = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"')
_CHANNEL_ID_JSON = re.compile(r'"(?:channelId|externalId)":"(UC[\w-]{22})"')


@dataclass
class Video:
    id: str
    title: str
    channel_id: str
    channel: str
    published_ts: float
    views: int
    url: str
    thumbnail: str = ""


class FeedError(ValueError):
    """Akış beklenen biçimde değil (engel/giriş sayfası ya da güvenilmeyen XML)."""


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return 0.0


def parse_feed(xml_text: str) -> list[Video]:
    """YouTube Atom akışını Video listesine çevirir. Hatalı/şüpheli XML FeedError fırlatır."""
    # Tanım (DTD/ENTITY) içeren XML'i hiç ayrıştırma: varlık genişletme saldırılarına karşı önlem
    head = xml_text[:4096].lower()
    if "<!doctype" in head or "<!entity" in head or "<!entity" in xml_text.lower():
        raise FeedError("güvenilmeyen XML (DTD/ENTITY)")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FeedError(f"XML ayrıştırılamadı: {exc}") from exc
    if not root.tag.endswith("feed"):
        raise FeedError("beklenmeyen yanıt (Atom akışı değil)")

    channel = (root.findtext("a:title", default="", namespaces=_NS) or "").strip()
    channel_id = (root.findtext("yt:channelId", default="", namespaces=_NS) or "").strip()
    if channel_id and not channel_id.startswith("UC"):
        channel_id = "UC" + channel_id  # akış kimliği "UC" öneki olmadan verir
    out: list[Video] = []
    for e in root.findall("a:entry", _NS):
        vid = (e.findtext("yt:videoId", default="", namespaces=_NS) or "").strip()
        title = (e.findtext("a:title", default="", namespaces=_NS) or "").strip()
        if not vid or not title:
            continue
        stats = e.find("m:group/m:community/m:statistics", _NS)
        try:
            views = int(stats.get("views", "0")) if stats is not None else 0
        except ValueError:
            views = 0
        thumb = e.find("m:group/m:thumbnail", _NS)
        out.append(Video(
            id=vid, title=title, channel_id=channel_id, channel=channel,
            published_ts=_ts(e.findtext("a:published", namespaces=_NS)),
            views=views, url=f"https://www.youtube.com/watch?v={vid}",
            thumbnail=thumb.get("url", "") if thumb is not None else "",
        ))
    return out


class YouTubeSource:
    name = "youtube"

    def __init__(self, http: HttpClient):
        self.http = http
        http.set_host_interval("www.youtube.com", 0.5)

    async def fetch_feed(self, channel_id: str) -> list[Video]:
        if not CHANNEL_ID_RE.match(channel_id):
            raise FeedError(f"geçersiz kanal kimliği: {channel_id[:40]}")
        text = await self.http.get_text(FEED_URL, params={"channel_id": channel_id}, retries=1)
        return parse_feed(text)

    async def resolve_channel(self, ref: str) -> str | None:
        """'UC...' kimliğini, '@tanıtıcı'yı ya da kanal adresini kanal kimliğine çevirir."""
        ref = ref.strip()
        if CHANNEL_ID_RE.match(ref):
            return ref
        m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", ref)
        if m:
            return m.group(1)
        handle = re.search(r"(@[\w.\-]{2,50})", ref)
        if not handle:
            return None
        page = await self.http.get_text(f"https://www.youtube.com/{handle.group(1)}", retries=1)
        found = _CANONICAL.search(page) or _CHANNEL_ID_JSON.search(page)
        return found.group(1) if found else None
