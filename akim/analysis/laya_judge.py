"""Laya ile yerel (ücretsiz) oynanış benzerliği.

Laya (Convai Innovations, Apache 2.0, https://github.com/NandhaKishorM/laya) metin üretmeyen bir
"karar modeli"dir: bir duruma (burada: PC oyunu + Roblox oyunu) tipli sorular sorulur, her seçenek
için olasılık döner. Halüsinasyon ve API ücreti yoktur; model yerelde çalışır (CPU'da çift başına
~1-3 sn, GPU'da ~35 ms).

İki sinyal üretilir:
  relation : "klon / esinlenme / aynı tür / ilgisiz" olasılıklarından beklenen benzerlik (0..1)
  embedding: aynı kodlayıcının iki açıklama için ürettiği vektörlerin kosinüs benzerliği

Bu depodaki ölçümler (eval/README.md) hazır checkpoint'in bu görevde eğitimsiz kullanımda zayıf
kaldığını gösteriyor; bu yüzden varsayılan kullanım "evidence" modudur: Laya diğer kanıtlara düşük
güvenilirlikle eklenir. Bu göreve ince ayar yapılmış bir checkpoint ile "judge" modu (son söz Laya'da)
kullanılabilir; eğitim/ölçüm verisi için eval/export_laya_dataset.py.

Kurulum: pip install "akim[laya]"  (PyTorch gerektirir; CPU için:
         pip install torch --index-url https://download.pytorch.org/whl/cpu)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field

from ..config import LayaConfig
from ..models import RobloxGame
from .matching import title_key
from .similarity import GameContext

log = logging.getLogger(__name__)

QUESTIONS = {
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
}

# Olasılıklardan beklenen benzerlik: klon 1.0, esinlenme 0.75, aynı tür 0.35, ilgisiz 0
RELATION_VALUE = {"clone": 1.0, "inspired": 0.75, "same_genre": 0.35, "unrelated": 0.0}


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def pc_text(ctx: GameContext) -> str:
    tags = f" Tags: {', '.join(ctx.tags[:8])}." if ctx.tags else ""
    return f"{ctx.title}.{tags} {_clip(ctx.description, 500)}".strip()


def roblox_text(name: str, description: str) -> str:
    return f"{name}. {_clip(description, 500)}".strip()


def pair_state(ctx: GameContext, name: str, description: str) -> dict:
    return {"pc_game": pc_text(ctx), "roblox_game": roblox_text(name, description)}


@dataclass
class LayaVerdict:
    similarity: float  # 0..1 (relation olasılıklarından beklenen değer)
    relation: str
    probabilities: dict = field(default_factory=dict)
    confidence: float = 0.0
    embedding: float | None = None  # kosinüs benzerliği (-1..1)


def _fingerprint(ctx: GameContext, g: RobloxGame, checkpoint: str) -> str:
    raw = f"{checkpoint}|{pc_text(ctx)}|{roblox_text(g.name, g.description)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class LayaJudge:
    def __init__(self, cfg: LayaConfig, store, agent=None):
        self.cfg = cfg
        self.store = store
        self._agent = agent
        self._lock = threading.Lock()  # model tek iş parçacığında çalışsın
        self.available = bool(cfg.enabled)
        self.last_error: str | None = None
        self.checkpoint = cfg.model_path or f"convaiinnovations/laya/{cfg.checkpoint or 'english'}"
        if self.available and agent is None:
            try:
                import laya  # noqa: F401  isteğe bağlı bağımlılık
            except ImportError:
                log.error('Laya açık ama "laya" paketi yok: pip install "akim[laya]"')
                self.available = False

    # ------------------------------------------------------------------ model
    def _load(self):
        if self._agent is None:
            import laya

            t0 = time.time()
            kwargs = {"device": self.cfg.device} if self.cfg.device else {}
            if self.cfg.model_path:
                self._agent = laya.load(self.cfg.model_path, **kwargs)
            elif self.cfg.checkpoint:
                self._agent = laya.load("convaiinnovations/laya", subfolder=self.cfg.checkpoint, **kwargs)
            else:
                self._agent = laya.load("convaiinnovations/laya", **kwargs)
            log.info("Laya yüklendi: %s (%.1f sn)", self.checkpoint, time.time() - t0)
        return self._agent

    def _predict(self, ctx: GameContext, games: list[RobloxGame]) -> dict[int, LayaVerdict]:
        with self._lock:
            agent = self._load()
            states = [pair_state(ctx, g.name, g.description) for g in games]
            results = agent.predict_batch(states, QUESTIONS, batch_size=self.cfg.batch_size, sort_by_length=True)
            emb: list[float | None] = [None] * len(games)
            if self.cfg.use_embeddings:
                import laya
                import numpy as np

                embed = laya.embed_fn_from_agent(agent)
                vecs = embed([pc_text(ctx)] + [roblox_text(g.name, g.description) for g in games])
                u = vecs[0]
                emb = [float(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9)) for v in vecs[1:]]
        out: dict[int, LayaVerdict] = {}
        for g, res, e in zip(games, results, emb):
            ans = res["answers"]["relation"]
            probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}
            sim = sum(RELATION_VALUE.get(k, 0.0) * p for k, p in probs.items())
            out[g.universe_id] = LayaVerdict(
                round(sim, 3), ans.get("choice", ""), probs, float(ans.get("answer_confidence", 0.0)), e
            )
        return out

    # ------------------------------------------------------------------ genel API
    async def judge(self, ctx: GameContext, candidates: list[RobloxGame]) -> dict[int, LayaVerdict]:
        """universe_id -> LayaVerdict. Önbellekte olanlar yeniden hesaplanmaz; hata ana akışı durdurmaz."""
        if not self.available or not candidates:
            return {}
        tkey = title_key(ctx.title)
        out: dict[int, LayaVerdict] = {}
        todo: list[RobloxGame] = []
        for g in candidates:
            row = self.store.get_laya_score(tkey, g.universe_id)
            if row and row["fingerprint"] == _fingerprint(ctx, g, self.checkpoint):
                out[g.universe_id] = LayaVerdict(
                    row["similarity"], row["relation"], json.loads(row["probabilities"] or "{}"),
                    row["confidence"] or 0.0, row["embedding"],
                )
            else:
                todo.append(g)
        if not todo:
            return out
        try:
            t0 = time.time()
            fresh = await asyncio.to_thread(self._predict, ctx, todo)
            log.debug("Laya: %s için %d aday %.1f sn", ctx.title, len(todo), time.time() - t0)
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Laya değerlendirmesi başarısız (%s): %s", ctx.title, exc)
            return out
        now = time.time()
        for g in todo:
            v = fresh.get(g.universe_id)
            if v:
                self.store.save_laya_score(
                    tkey, g.universe_id, _fingerprint(ctx, g, self.checkpoint), v.similarity, v.relation,
                    json.dumps(v.probabilities), v.confidence, v.embedding, self.checkpoint, now,
                )
                out[g.universe_id] = v
        return out
