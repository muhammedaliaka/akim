"""Motorun uçtan uca davranışı: sahte kaynaklarla liste -> Roblox -> karar -> bildirim."""

import asyncio
import time

from aiohttp.test_utils import TestClient, TestServer

from akim.config import ChannelConfig, Config
from akim.engine import Engine
from akim.events import EventBus
from akim.http import HttpClient
from akim.models import ChartEntry, Decision, RobloxGame, StoreGame
from akim.notify import Notifier
from akim.notify.base import Channel
from akim.storage import Storage
from akim.web.server import create_app

DAY = 86400


class Capture(Channel):
    name = "capture"

    def __init__(self):
        super().__init__(ChannelConfig(enabled=True, min_priority="low"))
        self.sent = []

    async def send(self, alert):
        self.sent.append(alert)


class FakeSteam:
    def __init__(self):
        self.entries: list[ChartEntry] = []

    async def fetch_charts(self, charts):
        return list(self.entries), {}

    async def current_players(self, appid):
        return None


class FakeEpic:
    async def fetch_charts(self, collections, include_free=False):
        return [], {}


class FakeRoblox:
    def __init__(self):
        self.games: dict[int, RobloxGame] = {
            1: RobloxGame(universe_id=1, root_place_id=11, name="Grow a Garden", playing=90000, visits=10**10),
        }

    async def search(self, query):
        return [RobloxGame(**{**g.__dict__}) for g in self.games.values()]

    async def details(self, ids):
        return {i: RobloxGame(**{**self.games[i].__dict__}) for i in ids if i in self.games}


def indie_game(**kw):
    base = dict(
        key="steam:100", store="steam", store_id="100", title="Moss Diver",
        url="https://store.steampowered.com/app/100", publishers=["Tiny Pond"], developers=["Tiny Pond"],
        tags=["Indie", "Adventure"], release_ts=int(time.time() - 5 * DAY),
    )
    base.update(kw)
    return StoreGame(**base)


def aaa_game():
    return StoreGame(
        key="steam:200", store="steam", store_id="200", title="Mega Shooter 27",
        publishers=["Electronic Arts"], developers=["EA DICE"], release_ts=int(time.time() - 3 * DAY),
    )


def build():
    cfg = Config()
    cfg.epic.enabled = False
    store = Storage(":memory:")
    bus = EventBus()
    engine = Engine(cfg, store, HttpClient(), bus)
    engine.steam, engine.epic, engine.roblox = FakeSteam(), FakeEpic(), FakeRoblox()
    cap = Capture()
    engine.notifier = Notifier(cfg.notifications, store, bus, [cap])
    return engine, cap


async def scan_all(engine):
    for row in engine._due_games():
        await engine.scan_roblox(row["key"])


def test_full_lifecycle_alerts():
    async def scenario():
        engine, cap = build()
        # 1) ilk liste: bağımsız oyun #30, AAA oyun #1
        engine.steam.entries = [
            ChartEntry("steam_topsellers", 1, aaa_game()),
            ChartEntry("steam_topsellers", 30, indie_game()),
        ]
        await engine.poll_steam()
        due = [r["key"] for r in engine._due_games()]
        assert due == ["steam:100"], "büyük yayıncı Roblox'ta taranmamalı"
        await scan_all(engine)
        d = engine.store.get_decision("steam:100")
        assert d["decision"] == Decision.OPPORTUNITY.value
        assert [a.type for a in cap.sent] == ["opportunity"]
        assert "Roblox'ta henüz karşılığı YOK" in cap.sent[0].body

        # 2) sırada büyük sıçrama -> rank_surge bildirimi (fırsat devam ediyor)
        engine.steam.entries = [ChartEntry("steam_topsellers", 4, indie_game())]
        await engine.poll_steam()
        assert cap.sent[-1].type == "rank_surge"
        assert "#30 → #4" in cap.sent[-1].title

        # 3) Roblox'ta ilk klon çıkıyor
        engine.roblox.games[2] = RobloxGame(universe_id=2, root_place_id=22, name="Moss Diver [BETA] 🌊",
                                            playing=20, visits=15000)
        await engine.scan_roblox("steam:100")
        assert cap.sent[-1].type == "first_clone"
        assert "Moss Diver [BETA]" in cap.sent[-1].body

        # 4) klon patlıyor -> AKIM BAŞLADI
        engine.roblox.games[2].playing = 900
        await engine.refresh_clones()
        assert engine.store.get_decision("steam:100")["decision"] == Decision.RISING_TREND.value
        assert cap.sent[-1].type == "rising_trend"

        # 5) tekrar değerlendirme aynı bildirimi yeniden göndermemeli
        n = len(cap.sent)
        await engine.refresh_clones()
        await engine.evaluate("steam:100")
        assert len(cap.sent) == n
        await engine.http.close()

    asyncio.run(scenario())


def test_bootstrap_collapses_initial_alerts_into_summary():
    async def scenario():
        engine, cap = build()
        engine.bootstrapping = True
        engine.steam.entries = [
            ChartEntry("steam_topsellers", 3, indie_game()),
            ChartEntry("steam_topsellers", 5, indie_game(key="steam:101", store_id="101", title="Lantern Keeper")),
        ]
        await engine.poll_steam()
        await scan_all(engine)
        assert cap.sent == []
        await engine._finish_bootstrap()
        assert [a.type for a in cap.sent] == ["bootstrap"]
        assert "Moss Diver" in cap.sent[0].body and "Lantern Keeper" in cap.sent[0].body
        await engine.http.close()

    asyncio.run(scenario())


def test_source_failure_alert_after_three_failures():
    async def scenario():
        engine, cap = build()
        for _ in range(4):
            await engine.ingest("steam", [], {"topsellers": "timeout"})
        assert [a.type for a in cap.sent] == ["system"]
        engine.steam.entries = [ChartEntry("steam_topsellers", 1, indie_game())]
        await engine.poll_steam()
        assert cap.sent[-1].type == "system" and "yeniden" in cap.sent[-1].title
        await engine.http.close()

    asyncio.run(scenario())


def test_web_api():
    async def scenario():
        engine, _ = build()
        engine.cfg.web.token = "gizli"
        engine.steam.entries = [ChartEntry("steam_topsellers", 2, indie_game())]
        await engine.poll_steam()
        await scan_all(engine)
        async with TestClient(TestServer(create_app(engine, engine.cfg.web))) as client:
            r = await client.get("/healthz")
            assert (await r.json())["ok"] is True
            r = await client.get("/api/board?decision=opportunity")
            rows = await r.json()
            assert rows[0]["title"] == "Moss Diver" and rows[0]["best_rank"] == 2
            r = await client.get("/api/games/steam:100")
            detail = await r.json()
            assert detail["game"]["title"] == "Moss Diver" and detail["decision"]["decision"] == "opportunity"
            r = await client.post("/api/check", json={"title": "Moss Diver"})
            assert r.status == 401
            r = await client.post("/api/check", json={"title": "Moss Diver"}, headers={"Authorization": "Bearer gizli"})
            assert (await r.json())["status"] == "none"
            r = await client.get("/")
            assert "Akım" in await r.text()
        await engine.http.close()

    asyncio.run(scenario())


def test_telegram_commands_render():
    async def scenario():
        engine, _ = build()
        engine.steam.entries = [ChartEntry("steam_topsellers", 2, indie_game(title="Moss <Diver>"))]
        await engine.poll_steam()
        await scan_all(engine)
        text = await engine.command("firsatlar", "")
        assert "Moss &lt;Diver&gt;" in text  # HTML kaçışı
        assert "Akım durumu" in await engine.command("durum", "")
        assert "Roblox'ta doğrudan karşılığı bulunamadı" in await engine.command("kontrol", "Some Game")
        await engine.http.close()

    asyncio.run(scenario())


def test_same_game_on_steam_and_epic_is_merged():
    async def scenario():
        engine, cap = build()
        engine.cfg.epic.enabled = True
        steam = indie_game(title="Hollow Lantern")
        epic = indie_game(key="epic:ns1", store="epic", store_id="ns1", title="Hollow Lantern™",
                          url="https://store.epicgames.com/en-US/p/hollow-lantern", tags=[])
        engine.steam.entries = [ChartEntry("steam_topsellers", 3, steam)]
        await engine.ingest("epic", [ChartEntry("epic_top-sellers", 2, epic)], {})
        await engine.poll_steam()
        calls = []
        orig = engine.roblox.search

        async def counting_search(q):
            calls.append(q)
            return await orig(q)

        engine.roblox.search = counting_search
        await scan_all(engine)
        assert len(calls) == 1, "aynı oyun için Roblox bir kez aranmalı"
        rows = engine.board()
        assert len(rows) == 1 and rows[0]["also"][0]["store"] in ("steam", "epic")
        assert [a.type for a in cap.sent] == ["opportunity"], "iki mağaza için tek bildirim"
        await engine.http.close()

    asyncio.run(scenario())


def test_sparse_chart_entry_does_not_erase_details():
    from akim.analysis.indie import IndieAssessment

    store = Storage(":memory:")
    full = indie_game(is_early_access=True, image="https://img", description="desc")
    store.upsert_game(full, IndieAssessment(0.9, "indie", ["x"]), 1.0)
    sparse = StoreGame(key=full.key, store="steam", store_id=full.store_id, title="App 100")
    store.upsert_game(sparse, IndieAssessment(0.45, "indie", []), 2.0)
    g = store.get_game(full.key)
    assert g["title"] == "Moss Diver" and g["is_early_access"] == 1 and g["indie_score"] == 0.9
    assert g["image"] == "https://img" and g["last_seen"] == 2.0 and g["first_seen"] == 1.0
