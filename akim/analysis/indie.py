"""Bir oyunun "büyük marka" mı yoksa bağımsız / az bilinen bir yapımcıdan mı geldiğini tahmin eder.

Skor 0..1 arasıdır: 0 = AAA / dev yayıncı, 1 = tamamen bağımsız.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import StoreGame

# Dev yayıncılar / platform sahipleri: bu oyunlar kapsam dışı
MAJOR_PUBLISHERS = [
    "electronic arts", "ea sports", "ea games", "ea originals", "activision", "blizzard", "ubisoft",
    "take-two", "take two", "2k", "2k games", "rockstar", "bethesda", "microsoft", "xbox game studios",
    "mojang", "sony", "playstation", "square enix", "bandai namco", "capcom", "sega", "atlus", "konami",
    "warner bros", "wb games", "nintendo", "tencent", "level infinite", "proxima beta", "netease",
    "krafton", "pubg", "nexon", "epic games", "valve", "riot games", "mihoyo", "hoyoverse", "cognosphere",
    "cd projekt", "embracer", "plaion", "deep silver", "koei tecmo", "ncsoft", "nc", "kakao", "smilegate",
    "pearl abyss", "kuro games", "perfect world", "garena", "amazon games", "bungie", "codemasters",
    "gameloft", "ubisoft entertainment", "disney", "lucasfilm", "hasbro", "wizards of the coast",
    "remedy entertainment", "io interactive", "quantic dream", "focus entertainment",
    "thq nordic", "paradox interactive", "gearbox", "2k sports", "zenimax", "arc system works",
    "shift up", "hypergryph", "gryphline", "infold", "papergames", "seasun", "yostar", "com2us",
    "netmarble", "wemade", "gravity co", "jagex", "cygames", "marvelous", "nacon", "bigben",
    "505 games", "saber interactive", "techland", "frontier developments", "games workshop",
    "starbreeze", "psyonix", "mediatonic", "ea", "xbox", "playstation publishing", "sony interactive entertainment",
]

# Orta ölçekli / tanınmış bağımsız yayıncılar: kısmen bağımsız sayılır
MIDTIER_PUBLISHERS = [
    "devolver", "team17", "annapurna", "raw fury", "tinybuild", "hooded horse", "kepler interactive",
    "coffee stain", "humble games", "curve games", "private division", "daedalic", "behaviour",
    "dotemu", "aspyr", "skybound", "headup", "chucklefish", "finji", "fellow traveller", "playstack",
    "thunderful", "modern wolf", "neon doctrine", "whitethorn", "armor games", "dear villagers",
    "yogscast games", "landfall", "innersloth", "klei", "re-logic", "mega crit", "supergiant",
    "embark studios", "ninja kiwi", "giants software", "wube software", "iron gate", "ghost ship",
    "digital extremes", "grinding gear", "facepunch", "hopoo", "playtonic", "maximum entertainment",
    "merge games", "ravenscourt", "astragon", "toplitz", "ultimate games", "playway", "pqube",
    "nicalis", "limited run", "no more robots", "akupara", "freedom games", "surefire", "top hat studios",
    "mad mushroom", "hypetrain", "crytivo", "gun interactive", "gun media", "illfonic", "sloclap",
    "hello games", "stunlock", "keen games", "ci games", "scs software", "larian",
]

_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|co|corp|corporation|gmbh|ag|s\.?a\.?|s\.?r\.?l\.?|b\.?v\.?|pte|plc|oy|ab|"
    r"k\.?k\.?|kft|sp z o\.?o\.?|publishing|entertainment|interactive|studios?|games|software|digital|"
    r"company|group|holdings|europe|america|japan|global)\b\.?"
)


def normalize_company(name: str) -> str:
    t = name.lower().replace("®", "").replace("™", "")
    t = re.sub(r"[,;:\"'()]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _company_core(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIXES.sub(" ", normalize_company(name))).strip()


def _matches_any(name: str, patterns: list[str]) -> str | None:
    norm = normalize_company(name)
    for p in patterns:
        pn = normalize_company(p)
        if not pn:
            continue
        if re.search(r"(?<![\w-])" + re.escape(pn) + r"(?![\w-])", norm):
            return p
    return None


@dataclass
class IndieAssessment:
    score: float
    tier: str  # "major" | "midtier" | "indie"
    reasons: list[str] = field(default_factory=list)


@dataclass
class IndieClassifier:
    extra_major: list[str] = field(default_factory=list)
    extra_midtier: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)

    def assess(self, game: StoreGame) -> IndieAssessment:
        reasons: list[str] = []
        companies = [c for c in game.publishers + game.developers if c and c.strip()]
        majors = MAJOR_PUBLISHERS + self.extra_major
        mids = MIDTIER_PUBLISHERS + self.extra_midtier

        allowed = any(_matches_any(c, self.allow) for c in companies) if self.allow else False
        if not allowed:
            for c in companies:
                hit = _matches_any(c, majors)
                if hit:
                    return IndieAssessment(0.0, "major", [f"büyük yayıncı/geliştirici: {c}"])

        score = 0.45  # bilinmeyen yayıncı taban puanı
        tier = "indie"
        mid_hit = None if allowed else next((c for c in companies if _matches_any(c, mids)), None)
        if mid_hit:
            tier = "midtier"
            reasons.append(f"tanınmış bağımsız yayıncı: {mid_hit}")
        elif companies:
            reasons.append("az bilinen yayıncı")
        else:
            score -= 0.1
            reasons.append("yayıncı bilgisi yok")

        if "Indie" in game.tags:
            score += 0.25
            reasons.append("Steam 'Indie' etiketi")
        pubs = {_company_core(p) for p in game.publishers}
        devs = {_company_core(d) for d in game.developers}
        if pubs and devs and pubs & devs:
            score += 0.2
            reasons.append("kendi yayınlıyor")
        if game.is_early_access:
            score += 0.1
            reasons.append("erken erişim")
        if game.price_cents is not None and 0 < game.price_cents <= 3000:
            score += 0.05
        if game.store == "epic" and not game.tags:
            # Epic'te etiket bilgisi yok; bilinmeyen yayıncıya biraz daha kredi ver
            score += 0.1

        score = min(1.0, score)
        if tier == "midtier":
            score = min(score, 0.55)
        return IndieAssessment(round(score, 3), tier, reasons)
