"""Bağlamsal benzerlik: gerçek Steam/Roblox verisinden alınmış vakalar (tests/fixtures/similarity_cases.json)."""

import json
from pathlib import Path

import pytest

from akim.analysis.concepts import CandidateText, ConceptProfile, concept_similarity
from akim.analysis.similarity import GameContext, classify, heuristic_evidence
from akim.config import ScoringConfig

CASES = json.loads((Path(__file__).parent / "fixtures" / "similarity_cases.json").read_text(encoding="utf-8"))


def evaluate(title: str) -> dict[str, object]:
    case = CASES[title]
    ctx = GameContext(case["title"], case["desc"], case["tags"])
    cands = case["cands"] + case["extra"]
    evs = heuristic_evidence(ctx, [CandidateText(c["name"], c["desc"]) for c in cands])
    cfg = ScoringConfig()
    for c, ev in zip(cands, evs):
        classify(ev, cfg, has_context=True, has_description=bool(c["desc"]))
    return {c["name"]: ev for c, ev in zip(cands, evs)}


def test_counter_strike_clones_found_without_the_name():
    ev = evaluate("Counter-Strike 2")
    # Hiçbiri "CS2" veya "Counter-Strike 2" demiyor; 5v5 bomba kur/çöz oynanışı yakalanmalı
    for name in ("BloxStrike", "[🧤] Defusal"):
        assert ev[name].kind == "clone" and ev[name].score >= 0.75, (name, ev[name].reason())
    # test verisinde açıklaması kısaltılmış; canlı veride oynanış %84 ve klon çıkıyor
    assert ev["Counter Blox"].kind in ("clone", "similar") and ev["Counter Blox"].score >= 0.7
    # Genel nişancılar: aynı tür ama klon değil
    assert ev["RIVALS"].kind == "similar", ev["RIVALS"].reason()  # "1v1 to 5v5" rekabetçi nişancı
    for name in ("RIVALS", "🔥Hypershot", "KNIFE DUELS"):
        assert ev[name].kind != "clone", (name, ev[name].reason())
        assert ev[name].score < ev["BloxStrike"].score
    assert "taktik FPS (bomba kur/çöz)" in ev["BloxStrike"].to_dict()["shared_concepts"]


def test_peak_concept_clone_ranks_above_unrelated():
    ev = evaluate("PEAK")
    assert ev["[⛰️] SUMMIT"].kind == "clone"  # açıklamada "inspired by the viral title PEAK"
    assert ev["CLIMB [⛰️]"].kind in ("clone", "similar")  # farklı isim, aynı tırmanış döngüsü
    assert ev["CLIMB [⛰️]"].score > ev["Dig the Backyard"].score + 0.2
    assert ev["RV Cooked? [UPDATE]"].kind == "none"


def test_supermarket_copy_vs_other_store_sims():
    ev = evaluate("Supermarket Simulator")
    assert ev["Supermarket Simulator"].score >= 0.9
    assert ev["Superstore Simulator"].kind == "clone"  # açıklama neredeyse aynı, isim farklı
    assert ev["Superstore Simulator"].score > ev["Clothing Store Simulator"].score
    assert ev["My Squishy Box Store🥟"].score < ev["Clothing Store Simulator"].score


def test_ghost_hunting_and_extraction_horror():
    ph = evaluate("Phasmophobia")
    assert ph["🎃 SPECTER"].kind == "clone"
    lc = evaluate("Lethal Company")
    assert lc["Deadly Company"].kind == "clone"
    assert lc["Deadly Company"].score > lc["Lethal Ape Experience"].score
    assert lc["NPC or DIE! 💢"].kind == "none"


def test_social_deduction_clone():
    ev = evaluate("Among Us")
    assert ev["[⚡] Imposters & Roles | Among us"].kind == "clone"
    assert ev["🌎 World.io"].kind == "none"


def test_fusion_combines_independent_evidence():
    from akim.analysis.similarity import Evidence

    cfg = ScoringConfig()
    # Counter Blox (canlı): isim %57 + oynanış %84 -> klon
    ev = classify(Evidence(name=0.57, concept=0.84), cfg, has_context=True, has_description=True)
    assert ev.kind == "clone" and ev.score >= 0.8
    # Silverpeak RP ~ Silver Palace: kısmi isim + zayıf/geniş oynanış -> klon değil
    ev = classify(Evidence(name=0.62, concept=0.6), cfg, has_context=True, has_description=True)
    assert ev.kind != "clone"
    # açık atıf tek başına yeterli
    ev = classify(Evidence(reference=0.95), cfg, has_context=True, has_description=True)
    assert ev.kind == "clone" and ev.weight == 1.0
    # benzer oynanış kısmi ağırlık alır
    ev = classify(Evidence(concept=0.68), cfg, has_context=True, has_description=True)
    assert ev.kind == "similar" and ev.weight == cfg.similar_weight


def test_thin_broad_profile_cannot_claim_clone():
    # Epic'ten gelen tek cümlelik açıklama: yalnızca geniş "açık dünya" kavramı
    ctx = GameContext("Silver Palace", "Silver Palace is an open-world action adventure RPG centred around detective mysteries.")
    cands = [CandidateText("Silverpeak 🏡 RP", "Explore the open world town of Silverpeak! Roleplay, jobs, cars and houses.")]
    ev = heuristic_evidence(ctx, cands)[0]
    classify(ev, ScoringConfig(), has_context=True, has_description=True)
    assert ev.kind != "clone", ev.reason()


def test_same_name_different_gameplay_is_not_a_clone():
    ctx = GameContext("Moss Diver", "Explore sunken caves, collect glowing moss and escape the tide.", ["Exploration"])
    cands = [CandidateText("Moss Diver", "Obby with 50 stages! Jump and reach the end to win UGC hats.")]
    ev = heuristic_evidence(ctx, cands)[0]
    classify(ev, ScoringConfig(), has_context=True, has_description=True)
    assert ev.kind != "clone"
    assert "oynanış farklı" in ev.reason()


def test_concept_profile_prefers_description_over_side_tags():
    p = ConceptProfile.from_store(
        "PEAK", "PEAK is a co-op climbing game where the slightest mistake can spell your doom.",
        ["Multiplayer", "Online Co-Op", "Co-op", "Adventure", "Physics", "Exploration"],
    )
    assert max(p.concepts, key=p.concepts.get) == "climbing"


def test_empty_profile_is_safe():
    assert concept_similarity(ConceptProfile(), [CandidateText("x", "y")]) == [(0.0, [])]


@pytest.mark.parametrize("title", list(CASES))
def test_scores_are_bounded(title):
    for ev in evaluate(title).values():
        assert 0.0 <= ev.score <= 1.0 and ev.kind in ("clone", "similar", "none")
