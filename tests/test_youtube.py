"""YouTube fragmanlarından çıkmamış oyun yakalama: başlık ayrıştırma, akış, Steam doğrulaması, bildirim."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from akim.analysis.trailers import buzz_score, is_big_franchise, parse_trailer_title
from akim.models import Priority, StoreGame
from akim.sources.youtube import FeedError, Video, YouTubeSource, parse_feed
from tests.test_engine import build

FIXTURES = Path(__file__).parent / "fixtures"
CHANNEL = "UC" + "a" * 22
HOUR = 3600


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------- başlık ayrıştırma (gerçek başlıklar)
CASES = json.loads((FIXTURES / "youtube_titles.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["title"][:60] for c in CASES])
def test_real_titles(case):
    info = parse_trailer_title(case["title"])
    if case["expect"] is None:
        assert info is None, f"fragman sanıldı: {info}"
    else:
        assert info is not None, "fragman bulunamadı"
        assert [info.game, info.kind, info.released] == case["expect"]


def test_fixture_covers_both_sides():
    assert sum(1 for c in CASES if c["expect"]) >= 25 and sum(1 for c in CASES if not c["expect"]) >= 100


@pytest.mark.parametrize(
    "title, game",
    [
        ("Moss Garden - Official Reveal Trailer", "Moss Garden"),
        ("Moss Garden | Gameplay Trailer | Wishlist now on Steam", "Moss Garden"),
        ("OFFICIAL TRAILER: Moss Garden", "Moss Garden"),
        ("Moss Garden Announcement Trailer", "Moss Garden"),
        ("Moss Garden: Release Date Trailer", "Moss Garden"),
        ("Moss Garden [Teaser Trailer] 4K", "Moss Garden"),
    ],
)
def test_title_shapes(title, game):
    assert parse_trailer_title(title).game == game


@pytest.mark.parametrize(
    "title",
    [
        "Moss Garden Review - Is it worth it?", "Moss Garden Tier List", "Moss Garden DLC Trailer",
        "Moss Garden Soundtrack (Official Trailer Music)", "Official Trailer", "Trailer", "Reveal Trailer | 2026",
        "Moss Garden Update 1.2 Trailer", "Top 10 Upcoming Games Trailer Compilation",
    ],
)
def test_non_game_trailers_are_rejected(title):
    assert parse_trailer_title(title) is None


def test_big_franchise_filter():
    assert is_big_franchise("Call of Duty: Black Ops 7") and is_big_franchise("Marvel's Wolverine")
    assert is_big_franchise("CoD: Modern Warfare 4")  # kısaltma: Steam adıyla ("Call of Duty®") eşleşmez
    assert not is_big_franchise("Moss Garden") and not is_big_franchise("Steamworld Dig")


def test_buzz_is_bounded_and_monotonic():
    assert buzz_score(500, 24) == 0.0
    assert buzz_score(1_000_000, 24) > 0.95
    a, b, c = buzz_score(10_000, 24), buzz_score(60_000, 24), buzz_score(300_000, 24)
    assert 0 < a < b < c <= 1
    assert buzz_score(60_000, 1) >= buzz_score(60_000, 72)  # aynı izlenme, daha genç video = daha sıcak
    assert buzz_score(60_000, 24, velocity_per_hour=5_000) > buzz_score(60_000, 24, velocity_per_hour=0)


# ---------------------------------------------------------------------- RSS akışı
def test_parse_real_feed():
    videos = parse_feed((FIXTURES / "youtube_feed.xml").read_text(encoding="utf-8"))
    assert len(videos) == 7
    v = videos[0]
    assert v.id and v.title.endswith("Trailer") and v.channel == "Indie Game Trailers"
    assert v.channel_id == "UCSg5I8fGpBym4RCsP_xSx2w" and v.url.endswith(v.id)
    assert v.published_ts > 1.7e9 and v.views >= 0 and v.thumbnail.startswith("https://")


@pytest.mark.parametrize(
    "bad",
    [
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><feed xmlns="http://www.w3.org/2005/Atom"/>',
        "<html><body>Giriş yap</body></html>",
        "bu bir XML değil",
        '<?xml version="1.0"?><rss/>',
    ],
)
def test_untrusted_or_wrong_feeds_are_rejected(bad):
    with pytest.raises(FeedError):
        parse_feed(bad)


def test_resolve_channel_forms():
    class Http:
        def set_host_interval(self, *_):
            pass

        async def get_text(self, url, **kw):
            assert url == "https://www.youtube.com/@IGN"
            return '<link rel="canonical" href="https://www.youtube.com/channel/UCKy1dAqELo0zrOtPkf0eTMw">'

    src = YouTubeSource(Http())
    assert run(src.resolve_channel(CHANNEL)) == CHANNEL
    assert run(src.resolve_channel("https://www.youtube.com/channel/UCKy1dAqELo0zrOtPkf0eTMw")) == "UCKy1dAqELo0zrOtPkf0eTMw"
    assert run(src.resolve_channel("@IGN")) == "UCKy1dAqELo0zrOtPkf0eTMw"
    assert run(src.resolve_channel("saçma değer")) is None
    with pytest.raises(FeedError):
        run(src.fetch_feed("kötü-kimlik"))


# ---------------------------------------------------------------------- motor akışı
class FakeYouTube:
    def __init__(self):
        self.videos: list[Video] = []
        self.fail = False

    async def resolve_channel(self, ref):
        return ref

    async def fetch_feed(self, channel_id):
        if self.fail:
            raise ConnectionError("GET feed başarısız: Cannot connect to host www.youtube.com:443")
        return list(self.videos)


def video(title, views, hours_ago=24, vid=None, channel="IGN"):
    return Video(
        id=vid or f"v{abs(hash(title)) % 10**8}", title=title, channel_id=CHANNEL, channel=channel,
        published_ts=time.time() - hours_ago * HOUR, views=views, url=f"https://www.youtube.com/watch?v={vid or 'x'}",
        thumbnail="https://img.example/t.jpg",
    )


def store_game(title, *, coming_soon=True, publishers=("Tiny Pond",), kind="game", release_in_days=30):
    return StoreGame(
        key=f"steam:{abs(hash(title)) % 10**6}", store="steam", store_id="1", title=title,
        url="https://store.steampowered.com/app/1", publishers=list(publishers), developers=list(publishers),
        tags=["Indie", "Adventure"], description="A cozy moss gardening adventure with relaxing puzzle exploration.",
        release_ts=int(time.time() + release_in_days * 86400) if coming_soon else int(time.time() - 86400 * 400),
        coming_soon=coming_soon, kind=kind,
    )


def make(games=None, first_run=False):
    engine, cap = build()
    if not first_run:
        engine.store.set_kv("youtube_first_poll_done", "1")  # çalışan sistem: ilk tur özeti geçti
    engine.cfg.youtube.channels = [CHANNEL]
    engine.youtube = FakeYouTube()
    catalog = games or {}

    async def find_by_title(title):
        return catalog.get(title)

    engine.steam.find_by_title = find_by_title
    return engine, cap


def alerts(cap, type_="upcoming_trailer"):
    return [a for a in cap.sent if a.type == type_]


def test_upcoming_indie_trailer_alerts_once():
    async def scenario():
        engine, cap = make({"Moss Garden": store_game("Moss Garden")})
        engine.youtube.videos = [video("Moss Garden - Official Reveal Trailer", 200_000, vid="a1")]
        await engine.poll_youtube()
        [a] = alerts(cap)
        assert a.title == "🎬 Çıkmamış oyun ses getiriyor: Moss Garden" and a.priority == Priority.HIGH
        assert a.url.endswith("a1") and "Yakında" in a.body
        fields = dict(a.fields)
        assert fields["Çıkış"] != "bilinmiyor" and "izlenme" in fields["Fragman"] and "Mağaza" in fields
        # ikinci tur ve yeni video aynı oyun için tekrar bildirim üretmez
        engine.youtube.videos.append(video("Moss Garden | Gameplay Trailer", 400_000, vid="a2"))
        await engine.poll_youtube()
        assert len(alerts(cap)) == 1
        assert engine.store.get_upcoming("moss garden")["state"] == "upcoming" or engine.store.list_upcoming()

    run(scenario())


def test_released_or_big_publisher_or_expansion_never_alert():
    async def scenario():
        engine, cap = make({
            "Old Hit": store_game("Old Hit", coming_soon=False),
            "Mega War": store_game("Mega War", publishers=("Electronic Arts",)),
            "BALL x PIT": store_game("BALL x PIT", coming_soon=False),
        })
        engine.youtube.videos = [
            video("Old Hit - Official Reveal Trailer", 500_000, vid="b1"),
            video("Mega War - Official Reveal Trailer", 500_000, vid="b2"),
            video("BALL x PIT: Risen Ballbylon | Reveal Trailer | Launching on November 12", 500_000, vid="b3"),
            video("Star Wars: Galactic Racer - Official Launch Trailer", 500_000, vid="b4"),  # başlıkta "launch"
        ]
        await engine.poll_youtube()
        assert alerts(cap) == []
        states = {u["title_key"]: u["state"] for u in [engine.store.get_upcoming(k) for k in ("old hit", "mega war")] if u}
        assert states == {"old hit": "released", "mega war": "filtered"}
        assert engine.store.get_upcoming("ball x pit risen ballbylon")["state"] == "released"  # ana oyun çıkmış: ek paket

    run(scenario())


def test_unlisted_trailers_need_more_buzz_and_skip_big_franchises():
    async def scenario():
        engine, cap = make()
        engine.youtube.videos = [
            video("Quiet Cave - Official Reveal Trailer", 30_000, vid="c1"),  # orta ses (0.50): mağazasız için yetmez (0.55)
            video("Loud Cave - Official Reveal Trailer", 300_000, vid="c2"),
            video("Mega Cave - Official Reveal Trailer", 3_000_000, vid="c3"),  # çok izlenen: büyük yapım
            video("Call of Duty: Cave Ops - Official Reveal Trailer", 300_000, vid="c4"),
        ]
        await engine.poll_youtube()
        [a] = alerts(cap)
        assert "Loud Cave" in a.title and a.priority == Priority.MEDIUM  # mağazasız: asla yüksek öncelik değil
        assert "mağaza sayfası yok" in a.body
        states = {k: (engine.store.get_upcoming(k) or {}).get("state") for k in ("quiet cave", "mega cave")}
        assert states["quiet cave"] == "unlisted"

    run(scenario())


def test_first_run_marks_existing_as_seen_without_flooding():
    async def scenario():
        engine, cap = make({"Moss Garden": store_game("Moss Garden")})
        engine.bootstrapping = True
        engine.youtube.videos = [video("Moss Garden - Official Reveal Trailer", 200_000, vid="d1")]
        await engine.poll_youtube()
        assert alerts(cap) == [] and engine.store.get_upcoming("moss garden")["alerted_at"]
        engine.bootstrapping = False
        await engine.poll_youtube()
        assert alerts(cap) == []  # zaten görüldü

    run(scenario())


def test_steam_lookup_failure_is_retried_not_guessed():
    async def scenario():
        engine, cap = make()
        calls = []

        async def broken(title):
            calls.append(title)
            raise ConnectionError("steam yok")

        engine.steam.find_by_title = broken
        engine.youtube.videos = [video("Moss Garden - Official Reveal Trailer", 200_000, vid="e1")]
        await engine.poll_youtube()
        assert alerts(cap) == [] and engine.store.get_upcoming("moss garden") is None  # "mağazasız" diye varsayılmadı

        async def ok(title):
            return store_game("Moss Garden")

        engine.steam.find_by_title = ok
        await engine.poll_youtube()
        assert len(alerts(cap)) == 1

    run(scenario())


def test_roblox_status_is_extra_info_never_required():
    async def scenario():
        from tests.test_resilience import FlakyRoblox

        engine, cap = make({"Moss Garden": store_game("Moss Garden")})
        engine.roblox = FlakyRoblox()
        engine.youtube.videos = [video("Moss Garden - Official Reveal Trailer", 200_000, vid="f1")]
        await engine.poll_youtube()
        [a] = alerts(cap)
        assert "Roblox" in dict(a.fields)  # Roblox erişilebilir: ek bilgi

        engine2, cap2 = make({"Moss Garden": store_game("Moss Garden")})
        engine2.roblox = FlakyRoblox()
        engine2.roblox.down = True  # VPN kapalı: yine bildirim gelir, yalnızca Roblox satırı yok
        engine2.youtube.videos = [video("Moss Garden - Official Reveal Trailer", 200_000, vid="f2")]
        await engine2.poll_youtube()
        [b] = alerts(cap2)
        assert "Roblox" not in dict(b.fields) and "YOK" not in b.body

        engine3, cap3 = make({"Moss Garden": store_game("Moss Garden")})
        engine3.cfg.roblox.enabled = False  # Roblox tamamen kapalı
        engine3.youtube.videos = [video("Moss Garden - Official Reveal Trailer", 200_000, vid="f3")]
        await engine3.poll_youtube()
        assert len(alerts(cap3)) == 1

    run(scenario())


def test_all_feeds_failing_reports_plainly_after_six_tries_and_recovers():
    async def scenario():
        engine, cap = make()
        engine.youtube.fail = True
        for _ in range(5):
            await engine.poll_youtube()
        assert [a for a in cap.sent if a.type == "system"] == []  # yardımcı kaynak: kısa kopmada susar
        await engine.poll_youtube()
        [a] = [a for a in cap.sent if a.type == "system"]
        assert a.title == "YouTube verisi alınamıyor" and "Cannot connect" not in a.body
        engine.youtube.fail = False
        await engine.poll_youtube()
        assert [a for a in cap.sent if a.type == "system"][-1].title == "YouTube yeniden çalışıyor"

    run(scenario())


def test_digest_and_telegram_command_list_upcoming():
    async def scenario():
        engine, cap = make({"Moss Garden": store_game("Moss Garden")})
        engine.youtube.videos = [video("Moss Garden - Official Reveal Trailer", 200_000, vid="g1")]
        await engine.poll_youtube()
        assert await engine.send_digest() is True
        digest = alerts(cap, "digest")[0]
        assert "Yaklaşan oyunlar" in digest.body and "Moss Garden" in digest.body
        reply = await engine.command("yaklasan", "")
        assert "Moss Garden" in reply and "youtube.com" in reply

    run(scenario())


def test_old_or_unpopular_videos_are_ignored():
    async def scenario():
        engine, cap = make({"Moss Garden": store_game("Moss Garden")})
        engine.youtube.videos = [
            video("Moss Garden - Official Reveal Trailer", 200_000, hours_ago=24 * 30, vid="h1"),  # çok eski
            video("Moss Garden - Official Gameplay Trailer", 800, vid="h2"),  # çok az izlenen
        ]
        await engine.poll_youtube()
        assert alerts(cap) == [] and engine.store.get_upcoming("moss garden") is None

    run(scenario())


def test_first_poll_sends_one_summary_then_individual_alerts():
    """Yükseltme sonrası ilk tur: mevcut her oyun için ayrı bildirim yağmaz, tek özet gider."""
    async def scenario():
        engine, cap = make({"Moss Garden": store_game("Moss Garden"), "Pine Cave": store_game("Pine Cave")}, first_run=True)
        engine.youtube.videos = [
            video("Moss Garden - Official Reveal Trailer", 200_000, vid="i1"),
            video("Pine Cave - Official Reveal Trailer", 150_000, vid="i2"),
        ]
        await engine.poll_youtube()
        assert alerts(cap) == []
        [summary] = alerts(cap, "digest")
        assert "Fragman izleme başladı" in summary.title and "Moss Garden" in summary.body and "Pine Cave" in summary.body
        # aynı oyunlar bir daha bildirilmez; yeni bir oyun ayrı bildirim üretir
        engine.steam.find_by_title = _catalog_with(engine, {"New Reef": store_game("New Reef")})
        engine.youtube.videos.append(video("New Reef - Official Reveal Trailer", 250_000, vid="i3"))
        await engine.poll_youtube()
        [one] = alerts(cap)
        assert "New Reef" in one.title and len(alerts(cap, "digest")) == 1

    run(scenario())


def _catalog_with(engine, extra):
    previous = engine.steam.find_by_title

    async def find(title):
        return extra.get(title) or await previous(title)

    return find
