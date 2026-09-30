"""Laya entegrasyonu: sahte bir Laya ajanıyla (PyTorch ve model indirmesi gerekmez)."""

import asyncio

from akim.analysis.laya_judge import QUESTIONS, LayaJudge, pair_state
from akim.analysis.similarity import Evidence, GameContext, classify
from akim.config import LayaConfig, ScoringConfig
from akim.models import RobloxGame
from akim.storage import Storage


class FakeAgent:
    """Roblox açıklamasında 'bomb' geçiyorsa klon, yoksa ilgisiz diyen sahte Laya."""

    def __init__(self):
        self.calls = []

    def predict_batch(self, states, questions, batch_size=8, sort_by_length=True):
        self.calls.append(len(states))
        assert set(questions) == {"relation"}
        out = []
        for st in states:
            clone = "bomb" in st["roblox_game"].lower()
            probs = {"clone": 0.9, "inspired": 0.05, "same_genre": 0.03, "unrelated": 0.02} if clone else \
                    {"clone": 0.02, "inspired": 0.03, "same_genre": 0.15, "unrelated": 0.8}
            out.append({"answers": {"relation": {"choice": max(probs, key=probs.get), "probabilities": probs,
                                                 "answer_confidence": max(probs.values())}}})
        return out


CTX = GameContext("Counter-Strike 2", "Elite competitive tactical shooter.", ["FPS", "Tactical"])
GAMES = [
    RobloxGame(universe_id=1, root_place_id=1, name="Squad Ops", description="Plant the bomb or defuse it."),
    RobloxGame(universe_id=2, root_place_id=2, name="Pet Hatch", description="Hatch eggs and collect pets."),
]


def make(**cfg):
    agent = FakeAgent()
    judge = LayaJudge(LayaConfig(enabled=True, **cfg), Storage(":memory:"), agent=agent)
    return judge, agent


def test_state_and_questions_shape():
    st = pair_state(CTX, "Squad Ops", "Plant the bomb.\n\n   Or defuse it." + " x" * 400)
    assert st["pc_game"].startswith("Counter-Strike 2. Tags: FPS, Tactical.")
    assert st["roblox_game"].startswith("Squad Ops. Plant the bomb. Or defuse it.")  # boşluklar sadeleşir
    assert len(st["roblox_game"]) <= 520  # 512 token sınırına sığsın
    assert set(QUESTIONS["relation"]["criteria"]) == {"clone", "inspired", "same_genre", "unrelated"}


def test_expected_similarity_and_cache():
    async def run():
        judge, agent = make()
        out = await judge.judge(CTX, GAMES)
        assert out[1].relation == "clone" and out[1].similarity > 0.9
        assert out[2].relation == "unrelated" and out[2].similarity < 0.1
        await judge.judge(CTX, GAMES)
        assert agent.calls == [2], "önbellekteki adaylar yeniden hesaplanmamalı"
        changed = RobloxGame(universe_id=2, root_place_id=2, name="Pet Hatch", description="Now with bombs!")
        out = await judge.judge(CTX, [changed])
        assert agent.calls == [2, 1] and out[2].relation == "clone"

    asyncio.run(run())


def test_failure_never_breaks_pipeline():
    class Broken(FakeAgent):
        def predict_batch(self, *a, **k):
            raise RuntimeError("CUDA out of memory")

    async def run():
        judge = LayaJudge(LayaConfig(enabled=True), Storage(":memory:"), agent=Broken())
        assert await judge.judge(CTX, GAMES) == {}
        assert "memory" in judge.last_error

    asyncio.run(run())


def test_evidence_mode_raises_only_with_low_weight():
    cfg = ScoringConfig()
    # sezgisel hiçbir şey bulamadı, Laya %95 klon diyor: evidence modunda tek başına klon yapmaz
    ev = classify(Evidence(laya=0.95, laya_relation="clone"), cfg, has_context=True, has_description=True,
                  laya_mode="evidence", laya_reliability=0.35)
    assert ev.kind != "clone" and 0.3 < ev.score < 0.4
    # sezgisel orta düzeyde + Laya onaylıyor -> birlikte klon
    ev = classify(Evidence(concept=0.7, laya=0.9, laya_relation="clone"), cfg, has_context=True,
                  has_description=True, laya_mode="evidence", laya_reliability=0.35)
    assert ev.kind == "clone"
    # Laya "ilgisiz" diyorsa evidence modunda skoru düşürmez (yalnızca yükseltir)
    ev = classify(Evidence(concept=0.85, laya=0.05, laya_relation="unrelated"), cfg, has_context=True,
                  has_description=True, laya_mode="evidence", laya_reliability=0.35)
    assert ev.kind == "clone"


def test_judge_mode_gives_final_word():
    cfg = ScoringConfig()
    ev = classify(Evidence(concept=0.9, laya=0.1, laya_relation="unrelated"), cfg, has_context=True,
                  has_description=True, laya_mode="judge")
    assert ev.kind == "none" and ev.score == 0.1
    ev = classify(Evidence(laya=0.88, laya_relation="clone"), cfg, has_context=True, has_description=True,
                  laya_mode="judge")
    assert ev.kind == "clone"
    # Claude kararı her zaman önce gelir
    ev = classify(Evidence(laya=0.88, laya_relation="clone", llm=0.2, llm_relation="unrelated"), cfg,
                  has_context=True, has_description=True, laya_mode="judge")
    assert ev.kind == "none"


def test_engine_sends_top_search_results_even_if_heuristic_is_zero():
    from tests.test_engine import FakeRoblox, build

    async def run():
        engine, _ = build()
        engine.roblox = FakeRoblox()
        engine.roblox.games = {
            7: RobloxGame(universe_id=7, root_place_id=7, name="Squad Ops", playing=500, visits=10**6,
                          description="Plant the bomb or defuse it."),
        }
        agent = FakeAgent()
        engine.cfg.laya = LayaConfig(enabled=True, mode="judge", min_prescore=0.9)
        engine.laya = LayaJudge(engine.cfg.laya, engine.store, agent=agent)
        # Bağlamsız isim: sezgisel skor düşük; Laya yine de aramanın üst sırasını görmeli
        result = await engine.lookup_roblox(GameContext("Zero Point Tactics"))
        assert agent.calls == [1]
        kinds = {g.name: ev.kind for g, ev in result.matches}
        assert kinds.get("Squad Ops") == "clone"
        await engine.http.close()

    asyncio.run(run())
