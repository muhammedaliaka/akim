from datetime import datetime, timezone
from pathlib import Path

from akim.sources.epic import collection_slug, offer_to_game, parse_free_games, parse_storefront
from akim.sources.roblox import parse_search
from akim.sources.steam import parse_search_results, store_item_to_game

FIXTURES = Path(__file__).parent / "fixtures"


def test_steam_search_html_parsing():
    html = (FIXTURES / "steam_search.html").read_text()
    # bundle satırı (virgüllü appid listesi) atlanmalı
    html += (
        '<a href="https://store.steampowered.com/bundle/1/X/" data-ds-bundleid="1" data-ds-appid="1,2,3" '
        'class="search_result_row"><span class="title">Bundle &amp; Co</span></a>'
        # tek oyunluk paket: aynı appid ile gelir, oyunun sırasını ezmemeli
        '<a href="https://store.steampowered.com/sub/9/" data-ds-packageid="9" data-ds-appid="3513350" '
        'class="search_result_row"><span class="title">Wuthering Waves Pack</span></a>'
    )
    rows = parse_search_results(html)
    assert rows[0] == (3513350, "Wuthering Waves", "https://store.steampowered.com/app/3513350/Wuthering_Waves/")
    assert len(rows) == 2
    assert all(title != "Wuthering Waves Pack" for _, title, _ in rows)
    assert all(isinstance(r[0], int) for r in rows)


def test_steam_store_item_to_game():
    item = {
        "appid": 3164500,
        "name": "Schedule I",
        "type": 0,
        "is_early_access": True,
        "store_url_path": "app/3164500/Schedule_I",
        "basic_info": {"publishers": [{"name": "TVGS"}], "developers": [{"name": "TVGS"}], "short_description": "d"},
        "release": {"steam_release_date": 1742853611},
        "reviews": {"summary_filtered": {"review_count": 290389, "percent_positive": 97}},
        "best_purchase_option": {"final_price_in_cents": "1999"},
        "tags": [{"tagid": 492}, {"tagid": 599}],
        "assets": {"asset_url_format": "steam/apps/3164500/${FILENAME}?t=1", "header": "header.jpg"},
    }
    g = store_item_to_game(item, {492: "Indie", 599: "Simulation"})
    assert g.key == "steam:3164500"
    assert g.publishers == ["TVGS"] and g.tags == ["Indie", "Simulation"]
    assert g.price_cents == 1999 and g.is_early_access and g.kind == "game"
    assert g.url == "https://store.steampowered.com/app/3164500/Schedule_I"
    assert g.image.endswith("steam/apps/3164500/header.jpg?t=1")
    assert store_item_to_game({"appid": 431960, "name": "Wallpaper Engine", "type": 6}).kind == "software"


def _offer(title, ns, **kw):
    o = {
        "title": title,
        "namespace": ns,
        "offerType": "BASE_GAME",
        "categories": [{"path": "games"}],
        "seller": {"name": "Seller"},
        "developerDisplayName": "Dev Studio",
        "publisherDisplayName": "Pub A; Co-Publisher: Pub B",
        "catalogNs": {"mappings": [{"pageSlug": f"{ns}-slug", "pageType": "productHome"}]},
        "price": {"totalPrice": {"discountPrice": 1999}},
        "releaseDate": "2026-09-01T00:00:00.000Z",
        "keyImages": [{"type": "OfferImageWide", "url": "https://cdn/img.jpg"}],
    }
    o.update(kw)
    return {"offer": o}


def test_epic_offer_and_storefront_parsing():
    g = offer_to_game(_offer("Game A", "ns1")["offer"])
    assert g.key == "epic:ns1" and g.url.endswith("/p/ns1-slug")
    assert g.publishers == ["Pub A", "Pub B"] and g.developers == ["Dev Studio"]
    assert offer_to_game(_offer("Addon", "ns9", offerType="ADD_ON")["offer"]) is None

    data = {
        "data": {"Storefront": {"storefrontModulesPaginated": {"modules": [
            {"id": "top", "type": "subModules", "modules": [
                {"id": "x", "type": "group", "link": {"src": "/collection/top-sellers"},
                 "offers": [_offer("Game A", "ns1"), _offer("Game A Deluxe", "ns1"), _offer("Game B", "ns2")]},
            ]},
            {"id": "y", "type": "group", "link": {"src": "collection/most-played"}, "offers": [_offer("Game C", "ns3")]},
            {"id": "z", "type": "group", "link": {"src": "collection/unwanted"}, "offers": [_offer("Game D", "ns4")]},
        ]}}}
    }
    entries = parse_storefront(data, ["top-sellers", "most-played"])
    assert [(e.chart, e.rank, e.game.title) for e in entries] == [
        ("epic_top-sellers", 1, "Game A"),
        ("epic_top-sellers", 2, "Game B"),  # aynı namespace'in ikinci sürümü atlanır
        ("epic_most-played", 1, "Game C"),
    ]
    assert collection_slug({"link": {"src": "/browse?sortBy=x"}}) is None


def test_epic_free_games_only_active_promotions():
    active = _offer("Free Now", "f1")["offer"]
    active["promotions"] = {"promotionalOffers": [{"promotionalOffers": [
        {"startDate": "2026-09-25T15:00:00.000Z", "endDate": "2026-10-02T15:00:00.000Z",
         "discountSetting": {"discountPercentage": 0}}]}]}
    upcoming = _offer("Later", "f2")["offer"]
    upcoming["promotions"] = {"promotionalOffers": []}
    data = {"data": {"Catalog": {"searchStore": {"elements": [active, upcoming]}}}}
    entries = parse_free_games(data, now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert [e.game.title for e in entries] == ["Free Now"]


def test_roblox_search_parsing_skips_sponsored_and_non_games():
    data = {"searchResults": [
        {"contentGroupType": "Game", "contents": [
            {"universeId": 1, "rootPlaceId": 10, "name": "Schedule X", "playerCount": 5, "totalUpVotes": 3},
            {"universeId": 2, "rootPlaceId": 20, "name": "Ad", "isSponsored": True},
        ]},
        {"contentGroupType": "User", "contents": [{"contentId": 3}]},
    ]}
    games = parse_search(data)
    assert [(g.universe_id, g.name, g.playing) for g in games] == [(1, "Schedule X", 5)]
    assert games[0].url == "https://www.roblox.com/games/10"
