"""İsteğe bağlı Claude doğrulama katmanı.

Sezgisel skorların belirsiz kaldığı durumda ("aynı tür mü, klon mu?") mağaza oyununu ve en iyi
Roblox adaylarını Claude'a verir; her aday için 0-100 oynanış benzerliği ve ilişki türü döner.

* Anahtar: ANTHROPIC_API_KEY (veya `ant auth login` profili). Paket: `pip install "akim[llm]"`.
* Maliyet kontrolü: kararlar veritabanında önbelleklenir (aynı aday + aynı açıklama tekrar sorulmaz),
  saatlik çağrı sınırı vardır, yalnızca ön puanı anlamlı adaylar gönderilir.
* Hata durumunda (ağ, hız sınırı, reddetme) sistem sezgisel skorlarla devam eder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass

from ..config import LLMConfig
from ..models import RobloxGame
from .matching import title_key
from .similarity import GameContext

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You compare a PC game from Steam or the Epic Games Store with Roblox experiences and judge \
how closely each Roblox experience recreates the PC game's gameplay.

Judge by the core gameplay loop, objectives, mechanics, setting and presentation, using the descriptions. \
Names are weak evidence on their own: a Roblox game can be a clone under a completely different name \
(for example, a 5v5 plant-or-defuse-the-bomb shooter with a buy phase is a Counter-Strike clone), and a \
shared word in the name without shared gameplay means the games are unrelated. An explicit statement such \
as "inspired by <game>" is strong evidence.

For each candidate return:
- similarity: integer 0-100, how close the core gameplay is (90+ near copy, 70-89 same core loop with \
differences, 40-69 same subgenre or partial overlap, below 40 little in common)
- relation: "clone" (recreates the same core loop), "inspired" (clearly borrows the core idea with notable \
changes), "same_genre" (only the broad genre matches) or "unrelated"
- reason: at most 12 words, written in Turkish

Return exactly one result per candidate id."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "similarity": {"type": "integer"},
                    "relation": {"type": "string", "enum": ["clone", "inspired", "same_genre", "unrelated"]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "similarity", "relation", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


@dataclass
class Verdict:
    similarity: float  # 0..1
    relation: str
    reason: str


def _fingerprint(ctx: GameContext, g: RobloxGame) -> str:
    raw = f"{ctx.title}|{ctx.description[:600]}|{g.name}|{g.description[:600]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _trim(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_prompt(ctx: GameContext, candidates: list[RobloxGame]) -> str:
    lines = [
        "PC game:",
        f"Title: {ctx.title}",
        f"Tags: {', '.join(ctx.tags[:12]) or '-'}",
        f"Description: {_trim(ctx.description, 700) or '-'}",
        "",
        "Roblox candidates:",
    ]
    for i, g in enumerate(candidates, 1):
        lines.append(f"[{i}] Name: {_trim(g.name, 100)}")
        lines.append(f"    Description: {_trim(g.description, 450) or '-'}")
    return "\n".join(lines)


class LLMJudge:
    def __init__(self, cfg: LLMConfig, store, client=None):
        self.cfg = cfg
        self.store = store
        self._client = client
        self._calls: list[float] = []
        self.available = bool(cfg.enabled)
        self.last_error: str | None = None
        if self.available and self._client is None:
            try:
                import anthropic  # isteğe bağlı bağımlılık
            except ImportError:
                log.error('LLM doğrulama açık ama "anthropic" paketi yok: pip install "akim[llm]"')
                self.available = False
            else:
                self._client = anthropic.AsyncAnthropic(max_retries=2, timeout=90.0)

    def _budget_ok(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if t > now - 3600]
        return len(self._calls) < self.cfg.max_calls_per_hour

    async def judge(self, ctx: GameContext, candidates: list[RobloxGame]) -> dict[int, Verdict]:
        """universe_id -> Verdict. Önbellekte olanlar tekrar sorulmaz."""
        if not self.available or not candidates:
            return {}
        cache_key = title_key(ctx.title)
        out: dict[int, Verdict] = {}
        todo: list[RobloxGame] = []
        max_age = time.time() - self.cfg.cache_days * 86400
        for g in candidates:
            fp = _fingerprint(ctx, g)
            row = self.store.get_verdict(cache_key, g.universe_id)
            if row and row["fingerprint"] == fp and row["ts"] >= max_age:
                out[g.universe_id] = Verdict(row["similarity"], row["relation"], row["reason"])
            else:
                todo.append(g)
        if not todo:
            return out
        if not self._budget_ok():
            log.info("LLM saatlik çağrı sınırına ulaşıldı; %s sezgisel skorlarla değerlendirildi", ctx.title)
            return out
        self._calls.append(time.time())
        try:
            fresh = await self._ask(ctx, todo)
        except Exception as exc:  # doğrulama katmanı ana akışı asla durdurmaz
            self.last_error = str(exc)
            log.warning("LLM doğrulaması başarısız (%s): %s", ctx.title, exc)
            return out
        now = time.time()
        for g in todo:
            v = fresh.get(g.universe_id)
            if v:
                self.store.save_verdict(cache_key, g.universe_id, _fingerprint(ctx, g), v.similarity, v.relation,
                                        v.reason, self.cfg.model, now)
                out[g.universe_id] = v
        return out

    async def _ask(self, ctx: GameContext, candidates: list[RobloxGame]) -> dict[int, Verdict]:
        import anthropic

        try:
            response = await self._client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=8000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # güvenlik sınıflandırıcısı reddederse sunucu önerilen modele geçer
                system=SYSTEM_PROMPT,
                output_config={
                    "effort": self.cfg.effort,
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                },
                messages=[{"role": "user", "content": build_prompt(ctx, candidates)}],
            )
        except anthropic.RateLimitError as exc:
            raise RuntimeError(f"Claude hız sınırı: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise RuntimeError(f"Claude API hatası {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise RuntimeError(f"Claude'a bağlanılamadı: {exc}") from exc

        if response.stop_reason == "refusal":
            log.warning("Claude isteği reddetti (%s)", ctx.title)
            return {}
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text)
        out: dict[int, Verdict] = {}
        for item in data.get("results") or []:
            idx = int(item["id"]) - 1
            if 0 <= idx < len(candidates):
                sim = max(0, min(100, int(item["similarity"]))) / 100
                out[candidates[idx].universe_id] = Verdict(sim, item["relation"], str(item["reason"])[:160])
        return out
