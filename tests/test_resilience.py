"""Roblox'a ulaşılamadığında (VPN kapalı, engelli ağ) sistemin davranışı.

Temel ilke: "Roblox'a ulaşamadım" asla "Roblox'ta yok" olarak yorumlanmaz; sistem Roblox'suz da rapor üretir
ve bağlantı gelince otomatik doğrular.
"""

import asyncio

import pytest

from akim.analysis.scoring import RobloxAssessment, decide
from akim.config import ScoringConfig
from akim.diagnostics import explain_error, human_duration
from akim.http import HttpError
from akim.models import ChartEntry, Decision, Priority, RobloxStatus
from akim.sources.roblox import RobloxSource, RobloxUnavailable
from tests.test_engine import FakeRoblox, build, indie_game, scan_all

REASON = "adres çözülemedi (internet yok ya da site engelli)"


class FlakyRoblox(FakeRoblox):
    """Açılıp kapanabilen sahte Roblox (VPN kapalı/açık)."""

    def __init__(self):
        super().__init__()
        self.down = False
        self.empty = False

    async def search(self, query):
        if self.down:
            raise RobloxUnavailable(REASON, "Cannot connect to host apis.roblox.com:443")
        return [] if self.empty else await super().search(query)

    async def details(self, ids):
        if self.down:
            raise RobloxUnavailable(REASON)
        return await super().details(ids)

    async def probe(self):
        if self.down or self.empty:
            raise RobloxUnavailable(REASON)


def run(coro):
    return asyncio.run(coro)


def make():
    engine, cap = build()
    engine.roblox = FlakyRoblox()
    engine.cfg.scoring.unverified_alert_momentum = 0.3  # test oyunu orta momentumlu
    return engine, cap


# ---------------------------------------------------------------------- karar mantığı
def test_unknown_status_can_never_be_opportunity():
    cfg = ScoringConfig()
    unknown = RobloxAssessment(RobloxStatus.UNKNOWN, 0.5, 0, 0, 0)
    decision, score = decide(indie=1.0, momentum=1.0, roblox=unknown, cfg=cfg, min_indie=0.5)
    assert decision == Decision.WATCH and score > 0.5
    decision, _ = decide(indie=1.0, momentum=0.1, roblox=unknown, cfg=cfg, min_indie=0.5)
    assert decision == Decision.LOW
    decision, _ = decide(indie=0.2, momentum=1.0, roblox=unknown, cfg=cfg, min_indie=0.5)
    assert decision == Decision.FILTERED


# ---------------------------------------------------------------------- uçtan uca kesinti
def test_outage_reports_without_roblox_then_verifies_after_recovery():
    async def scenario():
        engine, cap = make()
        engine.roblox.down = True
        engine.steam.entries = [ChartEntry("steam_topsellers", 30, indie_game())]
        await engine.poll_steam()
        # Roblox henüz "yok" sayılmaz ve oyun bekleme listesindedir: tarama sonucu beklenir, bildirim yok
        assert cap.sent == []

        # tarayıcı iki kez ulaşamaz -> kesinti ilan edilir; hiçbir "YOK" kaydı oluşmaz
        for _ in range(2):
            with pytest.raises(RobloxUnavailable):
                await engine.scan_roblox("steam:100")
        assert engine.roblox_health.state == "down" and not engine.roblox_usable()
        assert engine.store.last_roblox_check("steam:100") is None
        await engine._evaluate_unscanned()

        d = engine.store.get_decision("steam:100")
        assert d["roblox_status"] == "unknown" and d["decision"] == Decision.WATCH.value
        assert [a.type for a in cap.sent] == ["rising_unverified"]
        text = cap.sent[0].title + cap.sent[0].body + str(cap.sent[0].fields)
        assert "YOK" not in text and "doğrulanamadı" in text
        assert cap.sent[0].priority in (Priority.MEDIUM, Priority.HIGH)

        # kısa kopma bildirim üretmez; eşik aşılınca TEK uyarı gider
        await engine._maybe_alert_outage()
        assert not [a for a in cap.sent if a.type == "system"]
        engine.roblox_health.down_since -= 11 * 60
        await engine._maybe_alert_outage()
        await engine._maybe_alert_outage()
        system = [a for a in cap.sent if a.type == "system"]
        assert len(system) == 1 and system[0].priority == Priority.HIGH
        body = system[0].title + system[0].body
        assert "VPN" in body and REASON in body
        assert "Cannot connect" not in body and "log" not in body.lower() and ".md" not in body

        # kesintide klon takibi atlanır (hata günlüğü şişmez)
        await engine.refresh_clones()

        # VPN hâlâ kapalı: yoklama başarısız, oyun taranmaz
        assert await engine._roblox_ready() is False
        # VPN açıldı: yoklama aralığı dolmadan beklenir; dolunca yoklama başarılı -> kesinti kapanır
        engine.roblox.down = False
        assert await engine._roblox_ready() is False
        engine.roblox_health.last_probe = 0
        assert await engine._roblox_ready() is True
        assert engine.roblox_health.state == "ok"
        recovered = [a for a in cap.sent if a.type == "system"][-1]
        assert recovered.title == "Roblox yeniden çalışıyor" and recovered.priority == Priority.LOW
        outage = engine.store.outages_since(0)[0]
        assert outage["source"] == "roblox" and outage["ended_at"] is not None

        await scan_all(engine)
        assert engine.store.get_decision("steam:100")["decision"] == Decision.OPPORTUNITY.value
        assert "opportunity" in [a.type for a in cap.sent]

    run(scenario())


def test_outage_state_survives_restart_without_realerting():
    async def scenario():
        engine, cap = make()
        engine.roblox.down = True
        engine.steam.entries = [ChartEntry("steam_topsellers", 30, indie_game())]
        await engine.poll_steam()
        for _ in range(2):
            with pytest.raises(RobloxUnavailable):
                await engine.scan_roblox("steam:100")
        assert engine.roblox_health.state == "down"
        engine.roblox_health.down_since -= 11 * 60
        await engine._maybe_alert_outage()
        assert engine.roblox_health.alerted

        from akim.engine import Engine
        from akim.http import HttpClient
        from akim.events import EventBus

        again = Engine(engine.cfg, engine.store, HttpClient(), EventBus())
        assert again.roblox_health.state == "down" and again.roblox_health.alerted

    run(scenario())


def test_roblox_disabled_still_produces_reports():
    async def scenario():
        engine, cap = make()
        engine.cfg.roblox.enabled = False
        engine.steam.entries = [ChartEntry("steam_topsellers", 30, indie_game())]
        await engine.poll_steam()
        d = engine.store.get_decision("steam:100")
        assert d["roblox_status"] == "unknown"
        assert [a.type for a in cap.sent] == ["rising_unverified"]
        assert engine.roblox_state()["state"] == "off"

    run(scenario())


def test_empty_search_is_not_treated_as_none():
    """Sağlıklı Roblox hiçbir sorguya boş dönmez; boş yanıt engel işaretidir, 'YOK' denmez."""
    async def scenario():
        engine, _ = make()
        engine.roblox = FlakyRoblox()
        engine.roblox.empty = True
        from akim.analysis.similarity import GameContext

        with pytest.raises(RobloxUnavailable):
            await engine.lookup_roblox(GameContext("Moss Diver"))
        assert engine.roblox_health.fail_streak == 1

    run(scenario())


def test_healthy_roblox_with_unrelated_results_is_a_real_none():
    async def scenario():
        engine, _ = make()
        from akim.analysis.similarity import GameContext

        result = await engine.lookup_roblox(GameContext("Moss Diver"))
        assert result.matches == [] and engine.roblox_health.fail_streak == 0

    run(scenario())


def test_game_specific_scan_error_backs_off_without_blocking_others():
    engine, _ = make()
    engine.steam.entries = [ChartEntry("steam_topsellers", 30, indie_game())]
    run(engine.poll_steam())
    assert [r["key"] for r in engine._due_games()] == ["steam:100"]
    engine._scan_error("steam:100", "Moss Diver", ValueError("beklenmedik biçim"))
    assert engine._due_games() == []  # geri çekilme süresince denenmez
    engine._scan_fail["steam:100"] = (1, 0.0)  # süre doldu
    assert [r["key"] for r in engine._due_games()] == ["steam:100"]


# ---------------------------------------------------------------------- kaynak hataları ve metinler
class StubHttp:
    def __init__(self, exc=None, data=None):
        self.exc, self.data = exc, data

    def set_host_interval(self, *_):
        pass

    async def get_json(self, url, **kw):
        if self.exc:
            raise self.exc
        return self.data


@pytest.mark.parametrize(
    "exc, expected",
    [
        (ConnectionError("GET x başarısız: Cannot connect to host apis.roblox.com:443 ssl:default [Name or service not known]"),
         "adres çözülemedi"),
        (ConnectionError("GET x başarısız: Connection reset by peer"), "bağlantı kesildi"),
        (HttpError(403, "https://apis.roblox.com/x", "forbidden"), "erişim reddedildi"),
        (HttpError(503, "https://apis.roblox.com/x"), "sunucu tarafında"),
        (ValueError("Expecting value: line 1 column 1 (char 0)"), "beklenmeyen yanıt"),
    ],
)
def test_roblox_source_turns_failures_into_unavailable(exc, expected):
    src = RobloxSource(StubHttp(exc=exc))
    with pytest.raises(RobloxUnavailable) as info:
        run(src.search("obby"))
    assert expected in str(info.value)
    assert info.value.detail  # ham ayrıntı yalnızca log için saklanır


def test_roblox_source_rejects_non_object_json_and_empty_probe():
    with pytest.raises(RobloxUnavailable):
        run(RobloxSource(StubHttp(data=["engel", "sayfası"])).search("x"))
    with pytest.raises(RobloxUnavailable):
        run(RobloxSource(StubHttp(data={"searchResults": []})).probe())


def test_query_specific_400_is_not_an_outage():
    assert run(RobloxSource(StubHttp(exc=HttpError(400, "https://games.roblox.com/v1/games"))).details([1])) == {}


def test_source_failure_alert_is_plain_language_and_actionable():
    async def scenario():
        engine, cap = build()
        raw = "GET https://store.steampowered.com/x başarısız: Cannot connect to host store.steampowered.com:443 [Name or service not known]"
        for _ in range(3):
            await engine._source_failure("steam", raw)
        alerts = [a for a in cap.sent if a.type == "system"]
        assert len(alerts) == 1
        a = alerts[0]
        assert a.title == "Steam verisi alınamıyor" and a.priority == Priority.HIGH
        assert "adres çözülemedi" in a.body and "bir şey yapman gerekmez" in a.body
        assert "Cannot connect" not in a.body and "log" not in a.body.lower() and "dosya" not in a.body.lower()

        # toparlanma: tek, kısa bilgi bildirimi ve kesinti kaydı kapanır
        engine.steam.entries = [ChartEntry("steam_topsellers", 30, indie_game())]
        await engine.poll_steam()
        ok = [a for a in cap.sent if a.type == "system"][-1]
        assert ok.title == "Steam yeniden çalışıyor" and ok.priority == Priority.LOW
        assert engine.store.outages_since(0)[0]["ended_at"] is not None

    run(scenario())


def test_explain_error_and_durations():
    assert "adres çözülemedi" in explain_error("Temporary failure in name resolution")
    assert "hız sınırı" in explain_error("HTTP 429 https://x")
    assert "sunucu tarafında" in explain_error("HTTP 502 https://x")
    assert "güvenli bağlantı" in explain_error("SSL: CERTIFICATE_VERIFY_FAILED")
    assert explain_error("") == "beklenmeyen bir hata oluştu"
    assert human_duration(30) == "1 dk'dan kısa"
    assert human_duration(125 * 60) == "2 sa 5 dk"
    assert human_duration(3 * 86400 + 2 * 3600) == "3 gün 2 sa"


# ---------------------------------------------------------------------- tarayıcı döngüsü (uçtan uca, hızlandırılmış zaman)
def test_scanner_loop_rides_out_a_vpn_drop(monkeypatch):
    """VPN kapalıyken başla -> Roblox'suz rapor + tek uyarı -> VPN açılınca otomatik doğrula ve FIRSAT bildir."""
    import akim.engine as engine_module

    real_sleep = asyncio.sleep

    async def fast_sleep(delay, *a, **k):
        await real_sleep(0.001)

    async def scenario():
        engine, cap = make()
        engine.cfg.roblox.probe_minutes = 0  # kesintide her turda yokla
        engine.cfg.roblox.outage_alert_minutes = 0  # uyarı hemen
        engine.roblox.down = True
        engine.bootstrapping = False
        engine.steam.entries = [ChartEntry("steam_topsellers", 30, indie_game())]
        await engine.poll_steam()

        monkeypatch.setattr(engine_module.asyncio, "sleep", fast_sleep)
        loop_task = asyncio.create_task(engine._scanner_loop())

        async def wake_until(predicate, what):
            for _ in range(600):
                if predicate():
                    return
                engine._wake_scanner.set()
                await real_sleep(0.01)
            raise AssertionError(f"zaman aşımı: {what}")

        try:
            await wake_until(lambda: engine.roblox_health.state == "down", "kesinti ilanı")
            await wake_until(lambda: any(a.type == "rising_unverified" for a in cap.sent), "Roblox'suz rapor")
            await wake_until(lambda: any(a.type == "system" for a in cap.sent), "kesinti uyarısı")
            assert len([a for a in cap.sent if a.type == "system"]) == 1
            assert engine.store.last_roblox_check("steam:100") is None  # "yok" yazılmadı

            engine.roblox.down = False  # VPN açıldı
            await wake_until(lambda: any(a.type == "opportunity" for a in cap.sent), "toparlanma + FIRSAT")
            assert engine.roblox_health.state == "ok"
            titles = [a.title for a in cap.sent if a.type == "system"]
            assert titles == ["Roblox'a ulaşılamıyor", "Roblox yeniden çalışıyor"]
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

    run(scenario())
