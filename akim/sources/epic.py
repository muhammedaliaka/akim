"""Epic Games Store veri kaynağı.

Epic'in GraphQL uç noktası Cloudflare arkasında olduğu için mağaza ana sayfasının
kullandığı herkese açık ``storefrontLayout`` verisi kullanılır. Bu veri Top Sellers,
Most Played, Trending, Most Popular, Top New Releases gibi koleksiyonları içerir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from ..http import HttpClient
from ..models import ChartEntry, StoreGame

log = logging.getLogger(__name__)

BACKEND = "https://store-site-backend-static-ipv4.ak.epicgames.com"
STORE = "https://store.epicgames.com/en-US"


def _parse_ts(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _split_names(value: str | None) -> list[str]:
    if not value:
        return []
    value = value.replace("Co-Publisher:", ",")
    parts = [p.strip() for chunk in value.split(";") for p in chunk.split(",")]
    return [p for p in parts if p]


def offer_to_game(offer: dict) -> StoreGame | None:
    """Epic 'offer' nesnesini StoreGame'e çevirir; oyun olmayanlar için None döner."""
    if offer.get("offerType") not in (None, "BASE_GAME"):
        return None
    categories = {c.get("path") for c in offer.get("categories") or []}
    if categories and "games" not in categories:
        return None
    namespace = offer.get("namespace")
    if not namespace:
        return None

    slug = None
    for mapping in (offer.get("catalogNs") or {}).get("mappings") or offer.get("offerMappings") or []:
        if mapping.get("pageType") == "productHome" and mapping.get("pageSlug"):
            slug = mapping["pageSlug"]
            break
    slug = slug or offer.get("productSlug") or offer.get("urlSlug")

    image = ""
    for wanted in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail", "OfferImageTall"):
        for img in offer.get("keyImages") or []:
            if img.get("type") == wanted and str(img.get("url", "")).startswith("http"):
                image = img["url"]
                break
        if image:
            break

    total = (offer.get("price") or {}).get("totalPrice") or {}
    price = total.get("discountPrice")
    original = total.get("originalPrice")
    discount = 0
    if isinstance(price, (int, float)) and isinstance(original, (int, float)) and original > 0:
        discount = round(100 * (1 - price / original))
    seller = (offer.get("seller") or {}).get("name")
    publishers = _split_names(offer.get("publisherDisplayName")) or ([seller.strip()] if seller else [])
    developers = _split_names(offer.get("developerDisplayName"))

    return StoreGame(
        key=f"epic:{namespace}",
        store="epic",
        store_id=namespace,
        title=(offer.get("title") or "").strip(),
        url=f"{STORE}/p/{slug}" if slug else STORE,
        developers=developers,
        publishers=publishers,
        release_ts=_parse_ts(offer.get("releaseDate") or offer.get("effectiveDate")),
        price_cents=int(price) if isinstance(price, (int, float)) else None,
        is_free=price == 0,
        image=image,
        description=offer.get("description") or "",
        discount_pct=max(0, discount),
    )


def _walk_modules(modules: list[dict]) -> Iterable[dict]:
    for m in modules or []:
        yield m
        yield from _walk_modules(m.get("modules") or [])


def collection_slug(module: dict) -> str | None:
    """Modülün bağlantısından koleksiyon adını çıkarır (ör. '/collection/top-sellers' -> 'top-sellers')."""
    src = ((module.get("link") or {}).get("src")) or ""
    if "collection/" in src:
        return src.rstrip("/").rsplit("collection/", 1)[1].split("?")[0] or None
    return None


def parse_storefront(data: dict, wanted: Iterable[str]) -> list[ChartEntry]:
    wanted_set = set(wanted)
    modules = ((data.get("data") or {}).get("Storefront") or {}).get("storefrontModulesPaginated") or {}
    entries: list[ChartEntry] = []
    seen_charts: set[str] = set()
    for module in _walk_modules(modules.get("modules") or []):
        slug = collection_slug(module)
        if not slug or slug not in wanted_set or slug in seen_charts:
            continue
        seen_charts.add(slug)
        rank = 0
        seen_keys: set[str] = set()
        for wrapper in module.get("offers") or []:
            game = offer_to_game(wrapper.get("offer") or {})
            if game is None or game.key in seen_keys:
                continue
            seen_keys.add(game.key)
            rank += 1
            entries.append(ChartEntry(chart=f"epic_{slug}", rank=rank, game=game))
    return entries


def parse_free_games(data: dict, now: datetime | None = None) -> list[ChartEntry]:
    now = now or datetime.now(timezone.utc)
    elements = (((data.get("data") or {}).get("Catalog") or {}).get("searchStore") or {}).get("elements") or []
    entries: list[ChartEntry] = []
    for offer in elements:
        promos = (offer.get("promotions") or {}).get("promotionalOffers") or []
        active = False
        for group in promos:
            for p in group.get("promotionalOffers") or []:
                start, end = _parse_ts(p.get("startDate")), _parse_ts(p.get("endDate"))
                disc = (p.get("discountSetting") or {}).get("discountPercentage")
                if start and end and start <= now.timestamp() <= end and disc == 0:
                    active = True
        if not active:
            continue
        game = offer_to_game(offer)
        if game:
            entries.append(ChartEntry(chart="epic_free", rank=len(entries) + 1, game=game))
    return entries


class EpicSource:
    name = "epic"

    def __init__(self, http: HttpClient, *, country: str = "US"):
        self.http = http
        self.country = country.upper()

    async def fetch_charts(
        self, collections: Iterable[str], include_free: bool = False
    ) -> tuple[list[ChartEntry], dict[str, str]]:
        entries: list[ChartEntry] = []
        errors: dict[str, str] = {}
        collections = list(collections)
        try:
            data = await self.http.get_json(
                f"{BACKEND}/storefrontLayout", params={"locale": "en-US", "country": self.country}
            )
            entries.extend(parse_storefront(data, collections))
            found = {e.chart.removeprefix("epic_") for e in entries}
            for missing in set(collections) - found:
                log.info("Epic koleksiyonu mağaza düzeninde bulunamadı: %s", missing)
        except Exception as exc:
            log.exception("Epic mağaza düzeni alınamadı")
            errors["storefront"] = str(exc)

        if include_free:
            try:
                data = await self.http.get_json(
                    f"{BACKEND}/freeGamesPromotions",
                    params={"locale": "en-US", "country": self.country, "allowCountries": self.country},
                )
                entries.extend(parse_free_games(data))
            except Exception as exc:
                log.warning("Epic ücretsiz oyunlar alınamadı: %s", exc)
                errors["free"] = str(exc)
        return entries, errors
