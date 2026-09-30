"""Etiketli Steam->Roblox çiftlerini Laya'nın değerlendirme/ince ayar JSONL biçimine aktarır.

    python eval/export_laya_dataset.py            # eval/laya_dataset_calib.jsonl + _holdout.jsonl
    laya-evals run eval/laya_dataset_holdout.jsonl --model english --json report.json

Her satır: {"state": {...}, "questions": {"relation": ...}, "expected": {"relation": "clone"}, "tags": [...]}
Aynı dosyalar Laya'nın ince ayar notebook'unda eğitim verisi olarak kullanılabilir
(https://github.com/NandhaKishorM/laya). Eğitim için calib, ölçüm için holdout kullanın.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from akim.analysis.laya_judge import QUESTIONS, pair_state  # noqa: E402
from akim.analysis.similarity import GameContext  # noqa: E402
from eval.run_eval import SETS, load_data  # noqa: E402


def export(which: str) -> Path:
    cands, labels = load_data(which)
    out = ROOT / "eval" / f"laya_dataset_{which}.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for game, labs in labels.items():
            g = cands[game]
            ctx = GameContext(g["title"], g["desc"], g["tags"])
            for c in g["cands"]:
                if c["name"] not in labs:
                    continue
                row = {
                    "state": pair_state(ctx, c["name"], c["desc"]),
                    "questions": {"relation": QUESTIONS["relation"]},
                    "expected": {"relation": labs[c["name"]]},
                    "tags": [which, game],
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
    print(f"{out.relative_to(ROOT)}: {n} örnek")
    return out


if __name__ == "__main__":
    for which in SETS:
        export(which)
