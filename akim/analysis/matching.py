"""Mağaza oyunu <-> Roblox oyunu eşleştirme.

İki sinyal kullanılır:
  1. İsim benzerliği: gürültü (emoji, [UPDATE], sürüm etiketleri) temizlenmiş isimler
     arasında ağırlıklı kelime örtüşmesi + karakter dizisi benzerliği.
  2. Açıklama sinyali: Roblox klonları sık sık "inspired by PEAK", "based on Lethal
     Company" gibi ifadeler kullanır. Bu, isim tutmasa bile güçlü bir kanıttır.

"Peak", "Halloween" gibi genel kelimelerden oluşan başlıklar için isim eşleşmesi
yalnızca neredeyse birebir aynıysa kabul edilir; aksi halde yanlış pozitif çok olur.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "game", "games", "vr"}

# Roblox oyun isimlerinde anlam taşımayan süsler
ROBLOX_NOISE = {
    "update", "updated", "upd", "new", "beta", "alpha", "release", "released", "event", "sale",
    "free", "ugc", "limited", "roblox", "rblx", "official", "wip", "demo", "test", "testing",
    "rework", "reworked", "remastered", "classic", "open", "fixed", "x2", "2x", "x3", "3x",
}

ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}

# Roblox'ta çok sık geçen, tek başına ayırt edici olmayan kelimeler
COMMON_WORDS = {
    "adventure", "alien", "aliens", "alive", "anime", "apocalypse", "arena", "army", "backrooms", "ball",
    "battle", "beast", "blade", "block", "blocks", "boat", "boxing", "brawl", "build", "builder", "building",
    "business", "bus", "cafe", "car", "cars", "castle", "cave", "christmas", "city", "clash", "climb",
    "company", "craft", "dark", "day", "dead", "defense", "delivery", "desert", "dig", "doors", "dragon",
    "drive", "driving", "dungeon", "empire", "escape", "factory", "fall", "farm", "fight", "fighter",
    "fighting", "fire", "fish", "fishing", "fly", "football", "forest", "fruit", "galaxy", "garden",
    "ghost", "golf", "grow", "gun", "guns", "halloween", "hero", "heroes", "hide", "home", "horror",
    "hospital", "hotel", "house", "hunt", "hunter", "hunting", "ice", "island", "jump", "jungle", "king",
    "kingdom", "knight", "lab", "land", "lava", "legend", "legends", "life", "light", "magic", "mall",
    "market", "mech", "mine", "mining", "monster", "monsters", "mountain", "murder", "mystery", "night",
    "ninja", "ocean", "office", "outlaw", "park", "parkour", "peak", "pet", "pets", "pirate", "pizza",
    "planet", "plane", "prison", "quest", "race", "racing", "ranch", "restaurant", "rift", "robot",
    "robots", "royale", "run", "runner", "rush", "school", "sea", "seek", "shadow", "ship", "shooter",
    "shop", "simulator", "sky", "slide", "sniper", "snow", "soccer", "space", "story", "store", "strike",
    "summit", "super", "survival", "survive", "sword", "swords", "tank", "tanks", "tower", "town", "train",
    "truck", "tycoon", "village", "war", "wars", "water", "witch", "wizard", "work", "world", "worlds",
    "zombie", "zombies", "zoo", "obby", "simulator", "legacy", "online", "tactics", "chronicles",
}

_EDITION_RE = re.compile(
    r"\b(?:(?:digital\s+)?(?:deluxe|standard|ultimate|definitive|complete|gold|premium|collector'?s|"
    r"anniversary|enhanced|special)\s+edition|game of the year(?:\s+edition)?|goty|remastered|enhanced|"
    r"director'?s cut|early access|playtest|demo)\b",
    re.I,
)
_SYMBOLS_RE = re.compile(r"[™®©]")
_ACRONYM_RE = re.compile(r"(?<![\w.])((?:[A-Za-z]\.){2,}[A-Za-z]?)\.?")
_BRACKETS_RE = re.compile(r"[\[\(\{【<][^\]\)\}】>]*[\]\)\}】>]")
# "inspired by the viral title PEAK", "inspired by the backrooms and lethal company" gibi
# ifadelerde araya birkaç kelime girebilir
_INSPIRED_STRONG_RE = re.compile(
    r"(inspired by|inspiration from|based on|based off|clone of|tribute to|fan ?made|fan game|remake of|"
    r"version of|similar to|parody of|our take on|homage to)(?:\W+\w+){0,6}\W*$"
)
_INSPIRED_WEAK_RE = re.compile(r"(if you like|if you liked|fans of|like)\W*$")


def _collapse_acronyms(text: str) -> str:
    """'R.E.P.O.' -> 'REPO'."""
    return _ACRONYM_RE.sub(lambda m: m.group(1).replace(".", ""), text)


def normalize(text: str, *, drop_brackets: bool = True) -> str:
    # NFKC "™" işaretini "TM" harflerine çevirir; bu yüzden semboller önce atılır
    t = unicodedata.normalize("NFKC", _SYMBOLS_RE.sub("", text or ""))
    t = _collapse_acronyms(t)
    t = t.lower()
    if drop_brackets:
        stripped = _BRACKETS_RE.sub(" ", t)
        # isim tamamen parantez içindeyse parantezi koru
        if re.search(r"\w", stripped):
            t = stripped
    t = t.replace("&", " and ").replace("'", "").replace("’", "")
    t = re.sub(r"[^\w\s]", " ", t).replace("_", " ")
    return re.sub(r"\s+", " ", t).strip()


def clean_title(title: str) -> str:
    """Mağaza başlığından sürüm/edition eklerini ve ™ gibi sembolleri atar (arama için)."""
    t = unicodedata.normalize("NFKC", _SYMBOLS_RE.sub("", title or ""))
    t = _EDITION_RE.sub("", t)
    t = re.sub(r"\s*[-–—:|]\s*$", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str, extra_noise: set[str] = frozenset()) -> list[str]:
    return [w for w in normalize(text).split() if w not in STOPWORDS and w not in extra_noise]


def token_weight(tok: str) -> float:
    return 0.25 if len(tok) <= 1 or tok in ROMAN or tok.isdigit() else 1.0


def _tok_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    # küçük yazım farkları ("compnay" ~ "company"); ilk harf aynı olmalı ki
    # "iracing" ~ "racing" gibi farklı kelimeler eşleşmesin
    if len(a) >= 4 and len(b) >= 4 and a[0] == b[0] and abs(len(a) - len(b)) <= 2:
        return SequenceMatcher(None, a, b).ratio() >= 0.85
    return False


def name_similarity(a: list[str], b: list[str]) -> float:
    """Ağırlıklı kelime kapsama + karakter benzerliği; 0..1."""
    if not a or not b:
        return 0.0
    wa = sum(token_weight(t) for t in a)
    wb = sum(token_weight(t) for t in b)
    cov_a = sum(token_weight(t) for t in a if any(_tok_eq(t, u) for u in b)) / wa
    cov_b = sum(token_weight(t) for t in b if any(_tok_eq(t, u) for u in a)) / wb
    token_score = (2 * cov_a + cov_b) / 3
    seq = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    if a[0][0] != b[0][0]:
        seq *= 0.7
    joined = 0.0
    if len(a) != len(b):  # "war dogs" ~ "wardogs"
        joined = SequenceMatcher(None, "".join(a), "".join(b)).ratio()
        joined = joined if joined >= 0.9 else 0.0
    return max(token_score, seq, joined)


def title_key(title: str) -> str:
    """Mağazalar arası aynı oyunu tanımak için anahtar ("Hades II" Steam/Epic -> "hades ii")."""
    return normalize(clean_title(title) or title)


def is_generic(tokens: list[str]) -> bool:
    distinct = [t for t in tokens if token_weight(t) == 1.0]
    if not distinct:
        return True
    if len(distinct) == 1:
        return distinct[0] in COMMON_WORDS or len(distinct[0]) <= 4
    return len(distinct) == 2 and all(t in COMMON_WORDS for t in distinct)


def search_queries(title: str, max_n: int = 3) -> list[str]:
    """Roblox'ta aranacak sorgu varyantları."""
    clean = clean_title(title) or title.strip()
    variants = [clean]
    for sep in (":", " - ", " – ", " — ", "|"):
        if sep in clean:
            head = clean.split(sep, 1)[0].strip()
            if len(head) >= 3:
                variants.append(head)
    m = re.match(r"^(.*?)[\s:]+(?:[IVX]+|\d+)$", clean)
    if m and len(m.group(1).strip()) >= 3:
        variants.append(m.group(1).strip())
    nodots = _collapse_acronyms(clean)
    if nodots != clean:
        variants.append(nodots)
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out[:max_n]


@dataclass
class TitleProfile:
    title: str
    tokens: list[str]
    # (ifade, düz geçişi yeterli mi). Başlıktan türetilmiş tek kelimelik varyantlar
    # ("Schedule I" -> "schedule") açıklamada yalnızca "inspired by" bağlamında sayılır.
    phrases: list[tuple[str, bool]] = field(default_factory=list)
    generic: bool = False

    @classmethod
    def from_title(cls, title: str) -> TitleProfile:
        clean = clean_title(title) or title
        tokens = _tokens(clean)
        full = normalize(clean)
        phrases: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for q in search_queries(title, max_n=5):
            p = normalize(q)
            if len(p.replace(" ", "")) < 3 or p in seen:
                continue
            seen.add(p)
            phrases.append((p, p == full or len(p.split()) >= 2))
        return cls(title=title, tokens=tokens, phrases=phrases, generic=is_generic(tokens))


def description_signal(profile: TitleProfile, description: str) -> tuple[float, str]:
    if not description:
        return 0.0, ""
    # "inspired by" ile oyun adı aynı cümlede/satırda olmalı: "…inspired by REPO. Tags: … lethal company"
    # bir etiket listesidir, Lethal Company'ye atıf değildir.
    raw = _collapse_acronyms(unicodedata.normalize("NFKC", description))
    segments = [
        normalize(seg, drop_brackets=False)
        for seg in re.split(r"[.!?\n\r|•]+|(?i:\btags?\s*[:\-])", raw)
        if seg
    ]
    best = (0.0, "")
    for phrase, plain_ok in profile.phrases:
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b")
        for seg in segments:
            for m in pattern.finditer(seg):
                window = seg[max(0, m.start() - 90) : m.start()]
                inspired = _INSPIRED_STRONG_RE.search(window) or _INSPIRED_WEAK_RE.search(window[-20:])
                if inspired:
                    return 0.95, f"açıklamada '{inspired.group(1)} … {phrase}' ifadesi"
                if not plain_ok:
                    continue
                score = 0.45 if profile.generic else 0.78
                if score > best[0]:
                    best = (score, "açıklamada oyunun adı geçiyor")
    return best


def match_score(profile: TitleProfile, name: str, description: str = "") -> tuple[float, str]:
    """Bir Roblox oyununun mağaza oyununa benzerliği (0..1) ve gerekçesi."""
    b = _tokens(name, ROBLOX_NOISE) or _tokens(name)
    name_s = name_similarity(profile.tokens, b)
    if profile.generic:
        exact = SequenceMatcher(None, " ".join(profile.tokens), " ".join(b)).ratio()
        if exact < 0.9:
            name_s = min(name_s, 0.5)
    desc_s, desc_reason = description_signal(profile, description)

    if desc_s > name_s:
        score, reason = desc_s, desc_reason
    else:
        score, reason = name_s, f"isim benzerliği %{round(name_s * 100)}"
    if name_s >= 0.6 and desc_s >= 0.6:
        score = min(1.0, score + 0.05)
        reason = f"isim %{round(name_s * 100)} + {desc_reason}"
    return round(score, 3), reason
