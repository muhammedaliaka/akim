from akim.analysis.indie import IndieClassifier
from akim.analysis.scoring import MomentumSignals, assess_roblox, decide, momentum_score
from akim.config import ScoringConfig
from akim.models import Decision, RobloxGame, RobloxStatus, StoreGame


def game(**kw) -> StoreGame:
    base = dict(key="steam:1", store="steam", store_id="1", title="X")
    base.update(kw)
    return StoreGame(**base)


# ---------------------------------------------------------------- bağımsızlık
def test_major_publisher_is_filtered():
    c = IndieClassifier()
    assert c.assess(game(publishers=["Electronic Arts"], developers=["EA CANADA"])).score == 0
    assert c.assess(game(publishers=["SEGA"], developers=["Creative Assembly"])).tier == "major"
    assert c.assess(game(publishers=["2K"], developers=["Firaxis Games"])).tier == "major"


def test_word_boundaries_prevent_false_major():
    c = IndieClassifier()
    # "inc" içindeki "nc" veya "12K" içindeki "2k" büyük yayıncı sayılmamalı
    assert c.assess(game(publishers=["2824908 Ontario Inc"])).tier == "indie"
    assert c.assess(game(publishers=["12K Studio"])).tier == "indie"


def test_self_published_indie_scores_high():
    c = IndieClassifier()
    a = c.assess(game(publishers=["TVGS"], developers=["TVGS"], tags=["Indie", "Simulation"], is_early_access=True))
    assert a.tier == "indie"
    assert a.score >= 0.9


def test_midtier_is_capped_and_allow_list_overrides():
    a = IndieClassifier().assess(game(publishers=["Devolver Digital"], developers=["Tiny Studio"], tags=["Indie"]))
    assert a.tier == "midtier" and a.score <= 0.55
    c = IndieClassifier(allow=["Devolver Digital"])
    assert c.assess(game(publishers=["Devolver Digital"], tags=["Indie"])).tier == "indie"


def test_extra_major_from_config():
    c = IndieClassifier(extra_major=["Tiny Giant"])
    assert c.assess(game(publishers=["Tiny Giant Games"])).tier == "major"


# ---------------------------------------------------------------- momentum
def test_fresh_rising_game_has_high_momentum():
    score, reasons = momentum_score(
        MomentumSignals(best_rank=8, chart_count=2, max_climb=15, is_new_entry=True, release_age_days=10)
    )
    assert score >= 0.8
    assert any("yeni girdi" in r for r in reasons)


def test_evergreen_game_without_surge_is_penalised():
    old, _ = momentum_score(MomentumSignals(best_rank=15, chart_count=3, release_age_days=4000))
    fresh, _ = momentum_score(MomentumSignals(best_rank=15, chart_count=3, release_age_days=20))
    assert old < 0.2 < fresh


def test_evergreen_game_with_real_surge_is_not_penalised():
    score, _ = momentum_score(MomentumSignals(best_rank=15, chart_count=1, max_climb=40, ccu_ratio=2.5, release_age_days=4000))
    assert score >= 0.5


# ---------------------------------------------------------------- roblox + karar
def rg(playing=0, visits=0, uid=1) -> RobloxGame:
    return RobloxGame(universe_id=uid, root_place_id=uid, name=f"g{uid}", playing=playing, visits=visits)


def test_roblox_assessment_states():
    cfg = ScoringConfig()
    assert assess_roblox([], None, cfg).status == RobloxStatus.NONE
    assert assess_roblox([rg(5, 20_000)], None, cfg).status == RobloxStatus.EARLY
    big = [rg(3000, 80_000_000, i) for i in range(10)]
    assert assess_roblox(big, None, cfg).status == RobloxStatus.SATURATED
    rising = assess_roblox([rg(900, 2_000_000)], baseline_playing=120, cfg=cfg)
    assert rising.status == RobloxStatus.RISING


def test_huge_same_genre_game_does_not_saturate_alone():
    cfg = ScoringConfig()
    rivals = RobloxGame(universe_id=9, root_place_id=9, name="RIVALS", playing=135_000, visits=18_000_000_000)
    a = assess_roblox([(rivals, cfg.similar_weight)], None, cfg)
    assert a.status in (RobloxStatus.NONE, RobloxStatus.EARLY) and a.saturation <= cfg.similar_weight
    assert a.clone_count == 0 and a.similar_count == 1 and a.total_playing == 0
    # türdaşın günlük dalgalanması "AKIM BAŞLADI" üretmez: artış yalnızca klonlardan ölçülür
    assert assess_roblox([(rivals, cfg.similar_weight)], baseline_playing=10, cfg=cfg).status != RobloxStatus.RISING


def test_decisions():
    cfg = ScoringConfig()
    none = assess_roblox([], None, cfg)
    assert decide(0.9, 0.7, none, cfg, 0.5)[0] == Decision.OPPORTUNITY
    assert decide(0.2, 0.9, none, cfg, 0.5)[0] == Decision.FILTERED
    # Roblox'ta yok ama gerçek bir yükseliş yok -> fırsat değil
    assert decide(0.9, 0.15, none, cfg, 0.5)[0] != Decision.OPPORTUNITY
    sat = assess_roblox([rg(3000, 80_000_000, i) for i in range(10)], None, cfg)
    assert decide(0.9, 0.9, sat, cfg, 0.5)[0] == Decision.SATURATED
    rising = assess_roblox([rg(900, 2_000_000)], 100, cfg)
    assert decide(0.9, 0.5, rising, cfg, 0.5)[0] == Decision.RISING_TREND


def test_sale_driven_climb_is_discounted():
    base = dict(best_rank=9, chart_count=2, max_climb=40, release_age_days=3500)
    organic, _ = momentum_score(MomentumSignals(**base))
    sale, reasons = momentum_score(MomentumSignals(**base, discount_pct=75))
    assert sale < 0.2 < organic
    assert any("indirimde" in r for r in reasons)


def test_old_game_entering_chart_on_sale_is_not_a_trend():
    organic, _ = momentum_score(MomentumSignals(best_rank=50, chart_count=2, is_new_entry=True, release_age_days=900))
    sale, _ = momentum_score(MomentumSignals(best_rank=50, chart_count=2, is_new_entry=True, release_age_days=900, discount_pct=60))
    fresh_sale, _ = momentum_score(MomentumSignals(best_rank=50, chart_count=2, is_new_entry=True, release_age_days=20, discount_pct=20))
    assert sale < organic
    assert fresh_sale > 0.4  # yeni oyunda küçük indirim akımı gizlememeli
