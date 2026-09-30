"""Steam veri kaynağı.

Kullanılan uç noktalar API anahtarı gerektirmez:
  * store search (filter=topsellers)            -> anlık en çok satanlar
  * IStoreTopSellersService/GetWeeklyTopSellers -> haftalık en çok satanlar (+ geçen hafta sırası)
  * ISteamChartsService/GetGamesByConcurrentPlayers -> anlık en çok oynananlar
  * IStoreBrowseService/GetItems                -> yayıncı, geliştirici, etiket, çıkış tarihi
  * ISteamUserStats/GetNumberOfCurrentPlayers   -> tek oyunun anlık oyuncu sayısı
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Iterable

from difflib import SequenceMatcher

from ..analysis.matching import title_key
from ..http import HttpClient
from ..models import ChartEntry, StoreGame

log = logging.getLogger(__name__)

API = "https://api.steampowered.com"
STORE = "https://store.steampowered.com"
CDN = "https://shared.fastly.steamstatic.com/store_item_assets/"

CHART_NAMES = {
    "topsellers": "steam_topsellers",
    "weekly_topsellers": "steam_weekly",
    "most_played": "steam_mostplayed",
}

_ROW_RE = re.compile(r'<a href="([^"]+)"([^>]*?)data-ds-appid="(\d+)"([^>]*)class="search_result_row', re.S)
_TITLE_RE = re.compile(r'<span class="title">(.*?)</span>', re.S)


def parse_search_results(results_html: str) -> list[tuple[int, str, str]]:
    """Steam arama sonucu HTML'inden (appid, başlık, url) listesi çıkarır.

    Paket/bundle satırları atlanır: tek oyunluk paketler de aynı appid ile gelir ve
    oyunun gerçek sırasını ezer. Aynı appid ikinci kez görülürse yok sayılır.
    """
    out: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    matches = list(_ROW_RE.finditer(results_html))
    for i, m in enumerate(matches):
        attrs = m.group(2) + m.group(4)
        if "data-ds-packageid" in attrs or "data-ds-bundleid" in attrs:
            continue
        appid = int(m.group(3))
        if appid in seen:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(results_html)
        t = _TITLE_RE.search(results_html[m.end() : end])
        if not t:
            continue
        seen.add(appid)
        url = html.unescape(m.group(1)).split("?")[0]
        out.append((appid, html.unescape(t.group(1)).strip(), url))
    return out


def store_item_to_game(item: dict, tag_names: dict[int, str] | None = None) -> StoreGame:
    """IStoreBrowseService/GetItems veya GetWeeklyTopSellers 'item' nesnesini StoreGame'e çevirir."""
    appid = int(item.get("appid") or item.get("id"))
    basic = item.get("basic_info") or {}
    release = item.get("release") or {}
    reviews = ((item.get("reviews") or {}).get("summary_filtered")) or {}
    purchase = item.get("best_purchase_option") or {}
    assets = item.get("assets") or {}

    tags: list[str] = []
    if tag_names:
        tag_ids = [t.get("tagid") for t in item.get("tags") or []] or item.get("tagids") or []
        tags = [tag_names[t] for t in tag_ids if t in tag_names]

    image = ""
    if assets.get("asset_url_format") and assets.get("header"):
        image = CDN + assets["asset_url_format"].replace("${FILENAME}", assets["header"])

    price = purchase.get("final_price_in_cents")
    path = item.get("store_url_path") or f"app/{appid}"
    return StoreGame(
        key=f"steam:{appid}",
        store="steam",
        store_id=str(appid),
        title=item.get("name") or f"App {appid}",
        url=f"{STORE}/{path}",
        developers=[d["name"] for d in basic.get("developers") or [] if d.get("name")],
        publishers=[p["name"] for p in basic.get("publishers") or [] if p.get("name")],
        tags=tags,
        release_ts=release.get("steam_release_date") or release.get("original_release_date"),
        price_cents=int(price) if price not in (None, "") else None,
        is_free=bool(item.get("is_free")),
        is_early_access=bool(item.get("is_early_access") or release.get("is_early_access")),
        image=image,
        review_count=reviews.get("review_count"),
        review_pct=reviews.get("percent_positive"),
        description=basic.get("short_description") or "",
        # GetItems 'type': 0 = oyun; diğerleri yazılım/araç (ör. Wallpaper Engine = 6)
        kind="game" if int(item.get("type") or 0) == 0 else "software",
        discount_pct=int(purchase.get("discount_pct") or 0),
    )


class SteamSource:
    name = "steam"

    def __init__(self, http: HttpClient, *, country: str = "US", language: str = "english", top_n: int = 100):
        self.http = http
        self.country = country.upper()
        self.language = language
        self.top_n = top_n
        self._tag_names: dict[int, str] | None = None

    # ------------------------------------------------------------------ yardımcılar
    def _ctx(self) -> dict:
        return {"language": self.language, "country_code": self.country}

    async def tag_names(self) -> dict[int, str]:
        if self._tag_names is None:
            try:
                data = await self.http.get_json(f"{STORE}/tagdata/populartags/{self.language}")
                self._tag_names = {int(t["tagid"]): t["name"] for t in data}
            except Exception as exc:  # etiketler kritik değil
                log.warning("Steam etiket listesi alınamadı: %s", exc)
                return {}
        return self._tag_names

    async def fetch_items(self, appids: Iterable[int]) -> dict[int, StoreGame]:
        """Uygulama detaylarını 50'lik gruplar halinde çeker."""
        ids = list(dict.fromkeys(int(a) for a in appids))
        tags = await self.tag_names()
        out: dict[int, StoreGame] = {}
        for i in range(0, len(ids), 50):
            batch = ids[i : i + 50]
            payload = {
                "ids": [{"appid": a} for a in batch],
                "context": self._ctx(),
                "data_request": {
                    "include_basic_info": True,
                    "include_tag_count": 20,
                    "include_release": True,
                    "include_reviews": True,
                    "include_assets": True,
                },
            }
            data = await self.http.get_json(
                f"{API}/IStoreBrowseService/GetItems/v1/", params={"input_json": json.dumps(payload)}
            )
            for item in (data.get("response") or {}).get("store_items") or []:
                if item.get("success") != 1:
                    continue
                game = store_item_to_game(item, tags)
                out[int(game.store_id)] = game
        return out

    # ------------------------------------------------------------------ listeler
    async def live_topsellers(self) -> list[ChartEntry]:
        entries: list[ChartEntry] = []
        start = 0
        while len(entries) < self.top_n:
            data = await self.http.get_json(
                f"{STORE}/search/results/",
                params={
                    "filter": "topsellers",
                    "infinite": "1",
                    "count": str(min(100, self.top_n - len(entries))),
                    "start": str(start),
                    "cc": self.country.lower(),
                    "l": self.language,
                    "category1": "998",  # yalnızca oyunlar
                },
            )
            rows = parse_search_results(data.get("results_html") or "")
            if not rows:
                break
            for appid, title, url in rows:
                game = StoreGame(key=f"steam:{appid}", store="steam", store_id=str(appid), title=title, url=url)
                entries.append(ChartEntry(chart=CHART_NAMES["topsellers"], rank=len(entries) + 1, game=game))
            start += len(rows)
        return entries[: self.top_n]

    async def weekly_topsellers(self) -> list[ChartEntry]:
        payload = {
            "country_code": self.country,
            "context": self._ctx(),
            "data_request": {"include_basic_info": True},
            "page_start": 0,
            "page_count": self.top_n,
        }
        data = await self.http.get_json(
            f"{API}/IStoreTopSellersService/GetWeeklyTopSellers/v1/", params={"input_json": json.dumps(payload)}
        )
        entries = []
        for r in (data.get("response") or {}).get("ranks") or []:
            item = r.get("item") or {"appid": r["appid"]}
            game = store_item_to_game(item)
            entries.append(
                ChartEntry(
                    chart=CHART_NAMES["weekly_topsellers"],
                    rank=int(r["rank"]),
                    game=game,
                    prev_rank=r.get("last_week_rank") or None,
                    meta={"consecutive_weeks": r.get("consecutive_weeks")},
                )
            )
        return entries

    async def most_played(self) -> list[ChartEntry]:
        data = await self.http.get_json(f"{API}/ISteamChartsService/GetGamesByConcurrentPlayers/v1/")
        entries = []
        for r in ((data.get("response") or {}).get("ranks") or [])[: self.top_n]:
            appid = int(r["appid"])
            game = StoreGame(key=f"steam:{appid}", store="steam", store_id=str(appid), title=f"App {appid}")
            entries.append(
                ChartEntry(
                    chart=CHART_NAMES["most_played"],
                    rank=int(r["rank"]),
                    game=game,
                    metric=float(r.get("concurrent_in_game") or 0),
                    meta={"peak_in_game": r.get("peak_in_game")},
                )
            )
        return entries

    async def fetch_charts(self, charts: Iterable[str]) -> tuple[list[ChartEntry], dict[str, str]]:
        """İstenen listeleri çeker, eksik detayları tamamlar. (girdiler, hatalar) döner."""
        entries: list[ChartEntry] = []
        errors: dict[str, str] = {}
        handlers = {
            "topsellers": self.live_topsellers,
            "weekly_topsellers": self.weekly_topsellers,
            "most_played": self.most_played,
        }
        for chart in charts:
            handler = handlers.get(chart)
            if handler is None:
                errors[chart] = "bilinmeyen Steam listesi"
                continue
            try:
                entries.extend(await handler())
            except Exception as exc:
                log.exception("Steam listesi alınamadı: %s", chart)
                errors[chart] = str(exc)

        if entries:
            try:
                details = await self.fetch_items(int(e.game.store_id) for e in entries)
            except Exception as exc:
                log.warning("Steam detayları alınamadı: %s", exc)
                details = {}
            for e in entries:
                full = details.get(int(e.game.store_id))
                if full:
                    e.game = full
        return entries, errors

    async def find_by_title(self, title: str) -> StoreGame | None:
        """İsimle Steam'de oyun bulur (etiket ve açıklama için). Emin olunamazsa None."""
        data = await self.http.get_json(
            f"{STORE}/api/storesearch/", params={"term": title, "l": self.language, "cc": self.country}, retries=1
        )
        want = title_key(title)
        best = None
        for item in (data.get("items") or [])[:5]:
            if item.get("type") != "app":
                continue
            ratio = SequenceMatcher(None, title_key(item.get("name", "")), want).ratio()
            if ratio >= 0.9 and (best is None or ratio > best[0]):
                best = (ratio, int(item["id"]))
        if not best:
            return None
        return (await self.fetch_items([best[1]])).get(best[1])

    async def current_players(self, appid: int) -> int | None:
        try:
            data = await self.http.get_json(
                f"{API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", params={"appid": str(appid)}, retries=1
            )
        except Exception as exc:
            log.debug("CCU alınamadı %s: %s", appid, exc)
            return None
        resp = data.get("response") or {}
        return int(resp["player_count"]) if resp.get("result") == 1 else None
