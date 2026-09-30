"""Mağaza oyunu <-> Roblox oyunu bağlamsal benzerliği.

Her aday için dört bağımsız kanıt toplanır ve tek bir "% benzer" oranına indirgenir:

  isim      : gürültüsü temizlenmiş isimlerin benzerliği        ("Schedule I" ~ "Schedule X")
  atıf      : açıklamada orijinal oyuna gönderme                 ("inspired by the viral title PEAK")
  oynanış   : açıklama ve etiketlerden çıkarılan oynanış örtüşmesi ("5v5, bombayı kur/çöz" ~ Counter-Strike)
  Claude    : (isteğe bağlı) LLM'in oynanış karşılaştırması       ("aynı çekirdek döngü: %92 klon")

Sınıflar:
  clone    : aynı oyunu oynatıyor (Roblox doygunluğunda tam ağırlık)
  similar  : benzer oynanış / aynı alt tür (kısmi ağırlık)
  none     : ilgisiz
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ScoringConfig
from .concepts import CONCEPT_LABELS, CandidateText, ConceptProfile, concept_similarity
from .matching import TitleProfile, description_signal, match_score

RELATION_LABELS = {"clone": "klon", "inspired": "esinlenme", "same_genre": "aynı tür", "unrelated": "ilgisiz"}


@dataclass
class GameContext:
    """Karşılaştırılan mağaza oyununun bilinen her şeyi: isim + açıklama + etiketler."""

    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.title_profile = TitleProfile.from_title(self.title)
        self.concept_profile = ConceptProfile.from_store(self.title, self.description, self.tags)

    @property
    def generic(self) -> bool:
        return self.title_profile.generic


@dataclass
class Evidence:
    name: float = 0.0
    reference: float = 0.0
    reference_reason: str = ""
    concept: float = 0.0
    shared_concepts: list[str] = field(default_factory=list)
    llm: float | None = None
    llm_relation: str | None = None
    llm_reason: str | None = None
    score: float = 0.0
    kind: str = "none"  # clone | similar | none
    weight: float = 0.0
    note: str = ""

    def reason(self) -> str:
        parts = []
        if self.llm is not None:
            rel = RELATION_LABELS.get(self.llm_relation or "", self.llm_relation or "")
            parts.append(f"Claude %{round(self.llm * 100)} {rel}" + (f": {self.llm_reason}" if self.llm_reason else ""))
        if self.name >= 0.4:
            parts.append(f"isim %{round(self.name * 100)}")
        if self.reference >= 0.4:
            parts.append(self.reference_reason)
        if self.concept >= 0.3:
            labels = ", ".join(CONCEPT_LABELS.get(c, c) for c in self.shared_concepts[:3])
            parts.append(f"oynanış %{round(self.concept * 100)}" + (f" ({labels})" if labels else ""))
        if self.note:
            parts.append(self.note)
        return " · ".join(parts) or "zayıf benzerlik"

    def to_dict(self) -> dict:
        return {
            "score": self.score, "kind": self.kind, "weight": self.weight, "name": self.name,
            "reference": self.reference, "concept": self.concept,
            "shared_concepts": [CONCEPT_LABELS.get(c, c) for c in self.shared_concepts],
            "llm": self.llm, "llm_relation": self.llm_relation, "llm_reason": self.llm_reason,
            "reason": self.reason(),
        }


def heuristic_evidence(ctx: GameContext, candidates: list[CandidateText]) -> list[Evidence]:
    """API gerektirmeyen üç sinyali (isim, atıf, oynanış) topluca hesaplar."""
    concepts = concept_similarity(ctx.concept_profile, candidates, ctx.title)
    out = []
    for cand, (c_score, shared) in zip(candidates, concepts):
        name, _ = match_score(ctx.title_profile, cand.name)  # yalnızca isim
        ref, ref_reason = description_signal(ctx.title_profile, cand.description)
        out.append(Evidence(name=name, reference=ref, reference_reason=ref_reason, concept=c_score, shared_concepts=shared))
    return out


# Her sinyalin tek başına ne kadar güvenilir olduğu. İsim en kolay yanıltan sinyaldir
# ("Hades" adlı her oyun Hades değildir); açık atıf ("inspired by X") en güvenilirdir.
RELIABILITY = {"name": 0.85, "reference": 1.0, "concept": 0.95}


def fuse(ev: Evidence) -> float:
    """Bağımsız kanıtları noisy-OR ile birleştirir: her biri "klon olma" ihtimalini artırır.

    Ör. Counter Blox: isim %57 + oynanış %84 -> birlikte %82.
    İsim %50 civarı kısmi örtüşme ("Silverpeak" ~ "Silver Palace") neredeyse hiç kanıt değildir;
    bu yüzden isim 0.5..1.0 aralığından 0..1'e ölçeklenir. Zayıf sinyaller (< %45) yok sayılır.
    """
    name_p = max(0.0, (ev.name - 0.5) / 0.5)
    miss = 1.0
    for value, key in ((name_p, "name"), (ev.reference, "reference"), (ev.concept, "concept")):
        if value >= (0.1 if key == "name" else 0.45):
            miss *= 1.0 - value * RELIABILITY[key]
    return 1.0 - miss


def classify(ev: Evidence, cfg: ScoringConfig, *, has_context: bool, has_description: bool) -> Evidence:
    """Kanıtları tek bir benzerlik oranı ve sınıfa indirger (yerinde günceller)."""
    if ev.llm is not None:
        # Claude oynanışı bütün bağlamıyla değerlendirdi; son söz onun
        ev.score = round(ev.llm, 3)
        if ev.llm_relation in ("clone", "inspired") and ev.llm >= cfg.clone_threshold - 0.12:
            ev.kind = "clone"
        elif ev.llm_relation != "unrelated" and ev.llm >= cfg.similar_threshold:
            ev.kind = "similar"
        else:
            ev.kind = "none"
    else:
        ev.score = round(fuse(ev), 3)
        # Aynı isim ama bambaşka oynanış: bağlam varsa isim tek başına klon kanıtı sayılmaz
        name_only = (
            ev.name >= cfg.clone_threshold and ev.reference < 0.4 and ev.concept < 0.3
            and has_context and has_description
        )
        clone = (
            ev.reference >= 0.9
            or (ev.name >= cfg.clone_threshold and not name_only)
            or (ev.score >= cfg.concept_clone_threshold and ev.concept >= 0.55)
        )
        if name_only:
            ev.score = round(ev.score * 0.8, 3)
            ev.note = "isim benziyor ama oynanış farklı görünüyor"
        if clone:
            ev.kind = "clone"
        elif ev.score >= cfg.similar_threshold:
            ev.kind = "similar"
        else:
            ev.kind = "none"
    ev.weight = 1.0 if ev.kind == "clone" else (cfg.similar_weight if ev.kind == "similar" else 0.0)
    return ev
