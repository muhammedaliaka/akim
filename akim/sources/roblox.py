"""Roblox veri kaynağı: oyun arama ve oyun istatistikleri (anahtar gerektirmez)."""

from __future__ import annotations

import logging
import uuid
from typing import Iterable

from ..http import HttpClient
from ..models import RobloxGame

log = logging.getLogger(__name__)

SEARCH_URL = "https://apis.roblox.com/search-api/omni-search"
GAMES_URL = "https://games.roblox.com/v1/games"


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

    async def search(self, query: str) -> list[RobloxGame]:
        data = await self.http.get_json(
            SEARCH_URL,
            params={"searchQuery": query, "sessionId": str(uuid.uuid4()), "pageType": "all"},
        )
        return parse_search(data)

    async def details(self, universe_ids: Iterable[int]) -> dict[int, RobloxGame]:
        """Ziyaret sayısı, oluşturulma tarihi, anlık oyuncu gibi detayları 50'lik gruplarla çeker."""
        ids = list(dict.fromkeys(int(u) for u in universe_ids))
        out: dict[int, RobloxGame] = {}
        for i in range(0, len(ids), 50):
            batch = ids[i : i + 50]
            data = await self.http.get_json(GAMES_URL, params={"universeIds": ",".join(map(str, batch))})
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
                )
        return out
