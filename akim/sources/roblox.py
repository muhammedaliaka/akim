"""Roblox veri kaynağı: oyun arama ve oyun istatistikleri (anahtar gerektirmez)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable

from ..diagnostics import explain_error
from ..http import HttpClient, HttpError
from ..models import RobloxGame

log = logging.getLogger(__name__)

SEARCH_URL = "https://apis.roblox.com/search-api/omni-search"
GAMES_URL = "https://games.roblox.com/v1/games"


class RobloxUnavailable(Exception):
    """Roblox'a ulaşılamıyor ya da yanıtı güvenilmez (VPN kapalı, engelli ağ, bozuk/boş yanıt).

    Bu durum "Roblox'ta yok" demek DEĞİLDİR; çağıranlar sonucu boş sayıp fırsat üretmemelidir.
    ``str(exc)`` kullanıcıya gösterilebilir sade Türkçe nedendir, ham ayrıntı ``detail`` içindedir.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def parse_search(data: dict) -> list[RobloxGame]:
    out: list[RobloxGame] = []
    for group in data.get("searchResults") or []:
        if group.get("contentGroupType") != "Game":
            continue
        for c in group.get("contents") or []:
            if c.get("isSponsored"):
                continue
            uid = c.get("universeId") or c.get("contentId")
            if not uid:
                continue
            out.append(
                RobloxGame(
                    universe_id=int(uid),
                    root_place_id=int(c.get("rootPlaceId") or 0),
                    name=c.get("name") or "",
                    description=c.get("description") or "",
                    creator=c.get("creatorName") or "",
                    playing=int(c.get("playerCount") or 0),
                    up_votes=int(c.get("totalUpVotes") or 0),
                    down_votes=int(c.get("totalDownVotes") or 0),
                )
            )
    return out


class RobloxSource:
    name = "roblox"

    def __init__(self, http: HttpClient, *, request_delay: float = 1.0):
        self.http = http
        http.set_host_interval("apis.roblox.com", request_delay)
        # oyun detay uç noktası daha sıkı hız sınırına sahip
        http.set_host_interval("games.roblox.com", request_delay * 1.5)

    async def _get(self, url: str, **kw: Any) -> dict:
        """JSON alır; ağ, engel ve bozuk yanıt hatalarını RobloxUnavailable'a çevirir."""
        try:
            data = await self.http.get_json(url, **kw)
        except HttpError as exc:
            if exc.status in (400, 404, 422):  # yalnızca bu sorguya özgü; Roblox'un geneli sağlıklı
                return {}
            raise RobloxUnavailable(explain_error(f"http {exc.status}"), str(exc)) from exc
        except ConnectionError as exc:
            raise RobloxUnavailable(explain_error(str(exc)), str(exc)) from exc
        except ValueError as exc:  # JSON değil: engel / giriş sayfası
            raise RobloxUnavailable(explain_error("beklenmeyen yanıt"), str(exc)) from exc
        if not isinstance(data, dict):
            raise RobloxUnavailable(explain_error("beklenmeyen yanıt"), f"{type(data).__name__} geldi")
        return data

    async def search(self, query: str, retries: int | None = None) -> list[RobloxGame]:
        data = await self._get(
            SEARCH_URL,
            params={"searchQuery": query, "sessionId": str(uuid.uuid4()), "pageType": "all"},
            retries=retries,
        )
        return parse_search(data)

    async def probe(self) -> None:
        """Roblox'un gerçekten ulaşılabilir olduğunu doğrular (VPN/engel tespiti için kanarya yoklaması).

        Sağlıklı Roblox, anlamsız bir sorguya bile oyun listesi döndürür; boş yanıt engel/sorun işaretidir.
        Arama ve ayrıntı uç noktaları farklı hostlarda olduğundan ikisi de denenir.
        """
        games = await self.search("obby", retries=1)
        if not games:
            raise RobloxUnavailable(explain_error("beklenmeyen yanıt"), "arama boş döndü")
        if not await self.details([games[0].universe_id], retries=1):
            raise RobloxUnavailable(explain_error("beklenmeyen yanıt"), "oyun ayrıntıları boş döndü")

    async def details(self, universe_ids: Iterable[int], retries: int | None = None) -> dict[int, RobloxGame]:
        """Ziyaret sayısı, oluşturulma tarihi, anlık oyuncu gibi detayları 50'lik gruplarla çeker."""
        ids = list(dict.fromkeys(int(u) for u in universe_ids))
        out: dict[int, RobloxGame] = {}
        for i in range(0, len(ids), 50):
            batch = ids[i : i + 50]
            data = await self._get(GAMES_URL, params={"universeIds": ",".join(map(str, batch))}, retries=retries)
            for g in data.get("data") or []:
                out[int(g["id"])] = RobloxGame(
                    universe_id=int(g["id"]),
                    root_place_id=int(g.get("rootPlaceId") or 0),
                    name=g.get("name") or "",
                    description=g.get("description") or "",
                    creator=(g.get("creator") or {}).get("name") or "",
                    playing=int(g.get("playing") or 0),
                    visits=int(g.get("visits") or 0),
                    favorites=int(g.get("favoritedCount") or 0),
                    created=g.get("created"),
                    updated=g.get("updated"),
                    genre=" / ".join(x for x in (g.get("genre_l1"), g.get("genre_l2")) if x),
                )
        return out
