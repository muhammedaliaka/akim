"""Claude doğrulama katmanı: sahte bir Anthropic istemcisiyle (gerçek API çağrısı yapılmaz)."""

import asyncio
import json
from types import SimpleNamespace

from akim.analysis.judge import OUTPUT_SCHEMA, LLMJudge, build_prompt
from akim.analysis.similarity import GameContext
from akim.config import LLMConfig
from akim.models import RobloxGame
from akim.storage import Storage


class FakeMessages:
    def __init__(self, responder):
        self.calls = []
        self.responder = responder

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responder(kwargs)


class FakeClient:
    def __init__(self, responder):
        self.beta = SimpleNamespace(messages=FakeMessages(responder))


def text_response(payload, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(payload))])


CTX = GameContext("Counter-Strike 2", "Elite competitive tactical shooter.", ["FPS", "Tactical"])
CANDS = [
    RobloxGame(universe_id=11, root_place_id=1, name="Bomb Squad 5v5", description="Plant or defuse the bomb."),
    RobloxGame(universe_id=22, root_place_id=2, name="Pet Hatch Simulator", description="Hatch eggs, collect pets."),
]


def judge_with(responder, **cfg):
    store = Storage(":memory:")
    client = FakeClient(responder)
    judge = LLMJudge(LLMConfig(enabled=True, **cfg), store, client=client)
    return judge, client.beta.messages


def test_request_shape_and_parsing():
    def responder(kw):
        return text_response({"results": [
            {"id": 1, "similarity": 91, "relation": "clone", "reason": "5v5 bomba kur/çöz, aynı döngü"},
            {"id": 2, "similarity": 150, "relation": "unrelated", "reason": "yumurta açma oyunu"},
            {"id": 9, "similarity": 50, "relation": "clone", "reason": "olmayan aday"},
        ]})

    async def run():
        judge, msgs = judge_with(responder)
        out = await judge.judge(CTX, CANDS)
        kw = msgs.calls[0]
        assert kw["model"] == "claude-opus-5-5"
        assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
        assert kw["output_config"]["effort"] == "low"
        assert kw["output_config"]["format"] == {"type": "json_schema", "schema": OUTPUT_SCHEMA}
        assert "thinking" not in kw  # Opus 5.5'te adaptif düşünme varsayılan
        assert "[1] Name: Bomb Squad 5v5" in kw["messages"][0]["content"]
        assert out[11].similarity == 0.91 and out[11].relation == "clone"
        assert out[22].similarity == 1.0  # 0-100 dışı değer kırpılır
        assert set(out) == {11, 22}  # geçersiz id yok sayılır

    asyncio.run(run())


def test_verdicts_are_cached_and_only_new_candidates_asked():
    def responder(kw):
        n = kw["messages"][0]["content"].count("] Name:")
        return text_response({"results": [
            {"id": i, "similarity": 80, "relation": "inspired", "reason": "benzer"} for i in range(1, n + 1)
        ]})

    async def run():
        judge, msgs = judge_with(responder)
        await judge.judge(CTX, CANDS)
        await judge.judge(CTX, CANDS)
        assert len(msgs.calls) == 1, "önbellekteki adaylar tekrar sorulmamalı"
        extra = RobloxGame(universe_id=33, root_place_id=3, name="Defusal", description="Buy phase, rounds.")
        out = await judge.judge(CTX, CANDS + [extra])
        assert len(msgs.calls) == 2 and "Defusal" in msgs.calls[1]["messages"][0]["content"]
        assert "Bomb Squad" not in msgs.calls[1]["messages"][0]["content"]
        assert set(out) == {11, 22, 33}
        # açıklama değişirse yeniden sorulur
        changed = RobloxGame(universe_id=11, root_place_id=1, name="Bomb Squad 5v5", description="Now a tycoon!")
        await judge.judge(CTX, [changed])
        assert len(msgs.calls) == 3

    asyncio.run(run())


def test_hourly_budget_and_failures_fall_back_silently():
    calls = {"n": 0}

    def responder(kw):
        calls["n"] += 1
        raise RuntimeError("ağ hatası")

    async def run():
        judge, msgs = judge_with(responder, max_calls_per_hour=1)
        assert await judge.judge(CTX, CANDS) == {}  # hata ana akışı durdurmaz
        assert judge.last_error
        assert await judge.judge(CTX, CANDS) == {}  # bütçe doldu: API çağrılmaz
        assert calls["n"] == 1

    asyncio.run(run())


def test_refusal_returns_no_verdicts():
    async def run():
        judge, _ = judge_with(lambda kw: text_response({}, stop_reason="refusal"))
        assert await judge.judge(CTX, CANDS) == {}

    asyncio.run(run())


def test_disabled_judge_never_calls():
    async def run():
        store = Storage(":memory:")
        client = FakeClient(lambda kw: text_response({"results": []}))
        judge = LLMJudge(LLMConfig(enabled=False), store, client=client)
        assert await judge.judge(CTX, CANDS) == {}
        assert client.beta.messages.calls == []

    asyncio.run(run())


def test_prompt_contains_context_and_trims():
    long = RobloxGame(universe_id=1, root_place_id=1, name="X", description="word " * 500)
    prompt = build_prompt(CTX, [long])
    assert "Tags: FPS, Tactical" in prompt and "Title: Counter-Strike 2" in prompt
    assert len(prompt) < 1500


def test_engine_uses_llm_verdict_over_heuristics():
    from tests.test_engine import FakeRoblox, build

    def responder(kw):
        # Claude: isim tutmasa da 1 numaralı aday klon; 2 numara alakasız
        return text_response({"results": [
            {"id": 1, "similarity": 88, "relation": "clone", "reason": "aynı dalış ve ganimet döngüsü"},
            {"id": 2, "similarity": 5, "relation": "unrelated", "reason": "bahçe oyunu"},
        ]})

    async def run():
        engine, _ = build()
        engine.roblox = FakeRoblox()
        engine.roblox.games = {
            5: RobloxGame(universe_id=5, root_place_id=5, name="Deep Salvage", playing=300, visits=10**6,
                          description="Dive deep, collect moss and sell loot before the tide."),
            1: engine.roblox.games[1],
        }
        engine.judge = LLMJudge(LLMConfig(enabled=True, min_prescore=0.0), engine.store, client=FakeClient(responder))
        ctx = GameContext("Moss Diver", "Dive into sunken caves and collect glowing moss.", ["Exploration", "Underwater"])
        result = await engine.lookup_roblox(ctx)
        kinds = {g.name: (ev.kind, ev.score, ev.llm_relation) for g, ev in result.matches}
        assert kinds["Deep Salvage"] == ("clone", 0.88, "clone")
        assert "Grow a Garden" not in kinds
        await engine.http.close()

    asyncio.run(run())
