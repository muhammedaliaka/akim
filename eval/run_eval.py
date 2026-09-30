"""Roblox eşleştirme değerlendirmesi: sezgisel motor vs Laya vs birleşik.

    python eval/run_eval.py                 # yalnızca sezgisel (hızlı)
    python eval/run_eval.py --laya          # Laya'yı da çalıştır (CPU'da birkaç dakika; sonuçlar önbelleklenir)

Veri: eval/candidates.json (canlı Roblox arama sonuçları), eval/labels.json (elle etiketler).
Metrikler:
  AUROC      : skorun "klon/esinlenme" adayları diğerlerinden ne kadar iyi ayırdığı (0.5 = şans)
  sıra doğr. : (klon/esinlenme > aynı tür > ilgisiz) sırasını koruyan aday çiftlerinin oranı
  F1         : sistemin "klon" dediği adayların doğruluğu (kesinlik/duyarlılık)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from akim.analysis.concepts import CandidateText  # noqa: E402
from akim.analysis.similarity import GameContext, classify, heuristic_evidence  # noqa: E402
from akim.config import ScoringConfig  # noqa: E402

GRADE = {"clone": 2, "inspired": 2, "same_genre": 1, "unrelated": 0}


def auroc(scores: list[float], positive: list[bool]) -> float:
    pos = [s for s, p in zip(scores, positive) if p]
    neg = [s for s, p in zip(scores, positive) if not p]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def ordinal_accuracy(rows: list[dict], key: str) -> float:
    """Aynı oyun içindeki, farklı derecedeki aday çiftlerinde sıranın doğru olma oranı."""
    good = total = 0
    by_game: dict[str, list[dict]] = {}
    for r in rows:
        by_game.setdefault(r["game"], []).append(r)
    for items in by_game.values():
        for a, b in combinations(items, 2):
            if GRADE[a["label"]] == GRADE[b["label"]]:
                continue
            total += 1
            hi, lo = (a, b) if GRADE[a["label"]] > GRADE[b["label"]] else (b, a)
            good += (hi[key] > lo[key]) + 0.5 * (hi[key] == lo[key])
    return good / total if total else float("nan")


def f1(pred: list[bool], positive: list[bool]) -> tuple[float, float, float]:
    tp = sum(p and y for p, y in zip(pred, positive))
    fp = sum(p and not y for p, y in zip(pred, positive))
    fn = sum(y and not p for p, y in zip(pred, positive))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return prec, rec, (2 * prec * rec / (prec + rec) if prec + rec else 0.0)


def pc_text(g: dict) -> str:
    return f"{g['title']}. Tags: {', '.join(g['tags'][:8])}. {g['desc'][:500]}"


def rb_text(c: dict) -> str:
    return f"{c['name']}. {' '.join(c['desc'].split())[:500]}"


SETS = {
    "calib": ("eval/candidates.json", "eval/labels.json"),  # sezgisel motorun kalibre edildiği set
    "holdout": ("eval/candidates_holdout.json", "eval/labels_holdout.json"),  # hiç görülmemiş oyunlar
}


def load_data(which: str = "calib"):
    c_path, l_path = SETS[which]
    cands = json.loads((ROOT / c_path).read_text(encoding="utf-8"))
    labels = json.loads((ROOT / l_path).read_text(encoding="utf-8"))
    return cands, {k: v for k, v in labels.items() if not k.startswith("_")}


def heuristic_rows(cands, labels) -> list[dict]:
    cfg = ScoringConfig()
    rows = []
    for game, labs in labels.items():
        g = cands[game]
        ctx = GameContext(g["title"], g["desc"], g["tags"])
        evs = heuristic_evidence(ctx, [CandidateText(c["name"], c["desc"]) for c in g["cands"]])
        for c, ev in zip(g["cands"], evs):
            if c["name"] not in labs:
                continue
            classify(ev, cfg, has_context=True, has_description=bool(c["desc"]))
            rows.append({"game": game, "name": c["name"], "label": labs[c["name"]], "heur": ev.score,
                         "heur_clone": ev.kind == "clone", "pc": pc_text(g), "rb": rb_text(c), "ev": ev,
                         "has_desc": bool(c["desc"])})
    return rows


LAYA_QUESTIONS = {
    # Biçim 1: dört seçenekli ilişki sorusu
    "relation": {
        "type": "choice",
        "instructions": "Compare the Roblox game with the PC game. How closely does the Roblox game recreate "
                        "the PC game's core gameplay loop (objectives, mechanics, setting)?",
        "criteria": {
            "clone": "It recreates the same core gameplay loop as the PC game",
            "inspired": "It borrows the PC game's core idea with notable changes",
            "same_genre": "Only the broad genre matches; the core gameplay loop is different",
            "unrelated": "The two games have little in common",
        },
    },
    # Biçim 2: iki seçenek (dokümantasyonun noul yerine önerdiği)
    "same_loop": {
        "type": "choice",
        "instructions": "Does the Roblox game have the same core gameplay loop as the PC game?",
        "criteria": {"same": "Yes: same objectives and mechanics", "different": "No: different objectives or mechanics"},
    },
    # Biçim 3: sıralı skor
    "closeness": {
        "type": "score",
        "instructions": "Rate how similar the Roblox game's gameplay is to the PC game's gameplay.",
        "criteria": ["nothing in common", "same broad genre only", "similar core idea", "same core gameplay"],
    },
}


def laya_rows(rows: list[dict], cache_path: Path, checkpoint: str | None) -> None:
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    key = lambda r: f"{r['game']}|{r['name']}"  # noqa: E731
    todo = [r for r in rows if key(r) not in cache]
    todo_emb = [r for r in rows if key(r) not in cache or "emb" not in cache[key(r)]]
    if todo or todo_emb:
        import laya
        import numpy as np

        t0 = time.time()
        agent = laya.load("convaiinnovations/laya", **({"subfolder": checkpoint} if checkpoint else {}))
        print(f"Laya yüklendi ({time.time() - t0:.1f} sn), {len(todo)} çift değerlendiriliyor…", flush=True)
        if todo:
            t0 = time.time()
            states = [{"pc_game": r["pc"], "roblox_game": r["rb"]} for r in todo]
            results = agent.predict_batch(states, LAYA_QUESTIONS, batch_size=8, sort_by_length=True)
            elapsed = time.time() - t0
            print(f"  {elapsed:.1f} sn ({elapsed / len(todo) * 1000:.0f} ms/çift, 3 soru)", flush=True)
            for r, res in zip(todo, results):
                a = res["answers"]
                cache[key(r)] = {
                    "relation": a["relation"]["probabilities"],
                    "same": a["same_loop"]["probabilities"].get("same", 0.0),
                    "closeness": a["closeness"]["score"],
                }
        # Gömme benzerliği: aynı kodlayıcının ortalama havuzlanmış vektörleri (karar başı çalışmaz)
        embed = laya.embed_fn_from_agent(agent)
        pcs = sorted({r["pc"] for r in todo_emb})
        pc_vec = dict(zip(pcs, embed(pcs)))
        for r, v in zip(todo_emb, embed([r["rb"] for r in todo_emb])):
            u = pc_vec[r["pc"]]
            cache[key(r)]["emb"] = float(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9))
        cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    for r in rows:
        v = cache[f"{r['game']}|{r['name']}"]
        p = v["relation"]
        r["laya_rel"] = p.get("clone", 0) + 0.75 * p.get("inspired", 0) + 0.35 * p.get("same_genre", 0)
        r["laya_rel_clone"] = max(p, key=p.get) in ("clone", "inspired")
        r["laya_same"] = v["same"]
        r["laya_close"] = v["closeness"] / 3.0
        r["laya_emb"] = v.get("emb", 0.0)


def laya_nli_rows(rows: list[dict], cache_path: Path) -> None:
    """Doğal dil çıkarımı kalıbı: öncül = Roblox açıklaması, hipotez = PC oyununun kendi tanıtımı."""
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    key = lambda r: f"{r['game']}|{r['name']}"  # noqa: E731
    todo = [r for r in rows if key(r) not in cache]
    if todo:
        import laya

        agent = laya.load("convaiinnovations/laya")
        t0 = time.time()
        # Her PC oyunu için soru farklı (hipotez PC açıklamasını içerir): oyun oyun toplu çalıştır
        by_pc: dict[str, list[dict]] = {}
        for r in todo:
            by_pc.setdefault(r["pc"], []).append(r)
        for pc, items in by_pc.items():
            q = {"nli": {
                "type": "choice",
                "instructions": "Does the description below describe the same kind of game as the reference?",
                "criteria": {"same": f"Yes. It plays like this game: {pc[:420]}",
                             "different": "No. It is a different kind of game"},
            }}
            res = agent.predict_batch([{"game": r["rb"]} for r in items], q, batch_size=8, sort_by_length=True)
            for r, x in zip(items, res):
                cache[key(r)] = {"nli": x["answers"]["nli"]["probabilities"].get("same", 0.0)}
        print(f"  NLI: {time.time() - t0:.1f} sn ({len(todo)} çift)", flush=True)
        cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    for r in rows:
        r["laya_nli"] = cache[key(r)]["nli"]


def report(rows: list[dict], keys: list[tuple[str, str, str | None]]) -> None:
    positive = [GRADE[r["label"]] == 2 for r in rows]
    print(f"\n{len(rows)} etiketli çift · {sum(positive)} klon/esinlenme · {len(rows) - sum(positive)} diğer\n")
    print(f"{'yöntem':38} {'AUROC':>6} {'sıra doğr.':>10} {'kesinlik':>9} {'duyarlılık':>10} {'F1':>5}")
    for key, label, flag in keys:
        scores = [r[key] for r in rows]
        line = f"{label:38} {auroc(scores, positive):6.3f} {ordinal_accuracy(rows, key):10.3f}"
        if flag:
            p, rc, f = f1([r[flag] for r in rows], positive)
            line += f" {p:9.2f} {rc:10.2f} {f:5.2f}"
        print(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--laya", action="store_true")
    ap.add_argument("--checkpoint", default=None, help="ör. typed-decisions, multilingual")
    ap.add_argument("--per-game", action="store_true")
    ap.add_argument("--set", default="calib", choices=list(SETS))
    ap.add_argument("--nli", action="store_true", help="Laya'yı doğal dil çıkarımı kalıbıyla da dene")
    args = ap.parse_args()

    cands, labels = load_data(args.set)
    print(f"Veri seti: {args.set}")
    rows = heuristic_rows(cands, labels)
    keys = [("heur", "Akım sezgisel (isim+atıf+oynanış)", "heur_clone")]
    if args.laya:
        cache = ROOT / "eval" / f"laya_cache_{args.set}_{args.checkpoint or 'english'}.json"
        laya_rows(rows, cache, args.checkpoint)
        import copy

        from akim.analysis.similarity import classify as _classify

        cfg = ScoringConfig()
        for r in rows:
            r["blend"] = 0.5 * r["heur"] + 0.5 * r["laya_rel"]
            # Akım'ın gerçek entegrasyonu: evidence modu (yalnızca yükseltir, düşük güven)
            ev = copy.deepcopy(r["ev"])
            ev.laya, ev.laya_relation = r["laya_rel"], None
            _classify(ev, cfg, has_context=True, has_description=r["has_desc"], laya_mode="evidence",
                      laya_reliability=0.35)
            r["akim_laya"], r["akim_laya_clone"] = ev.score, ev.kind == "clone"
        keys += [
            ("laya_rel", "Laya: 4 seçenekli ilişki", "laya_rel_clone"),
            ("laya_same", "Laya: aynı döngü mü (evet/hayır)", None),
            ("laya_close", "Laya: sıralı benzerlik skoru", None),
            ("laya_emb", "Laya kodlayıcı gömme benzerliği", None),
            ("blend", "Karışım: 0.5 sezgisel + 0.5 Laya", None),
            ("akim_laya", "Akım + Laya evidence modu (r=0.35)", "akim_laya_clone"),
        ]
    if args.nli:
        laya_nli_rows(rows, ROOT / "eval" / f"laya_cache_{args.set}_nli.json")
        keys.append(("laya_nli", "Laya: doğal dil çıkarımı kalıbı", None))
    report(rows, keys)
    if args.per_game:
        for game in labels:
            sub = [r for r in rows if r["game"] == game]
            pos = [GRADE[r["label"]] == 2 for r in sub]
            parts = [f"{k}={auroc([r[k] for r in sub], pos):.2f}" for k, _, _ in keys]
            print(f"  {game:22} " + " ".join(parts))


if __name__ == "__main__":
    main()
