"""YouTube video başlıklarından oyun fragmanlarını ve oyun adlarını çıkarır, "ses getirme" puanı hesaplar.

Başlıklar serbest metindir ("At Fate's End: Official Gameplay Trailer", "Uncanyon | Reveal Trailer",
"Oniria - Official Trailer | Out of Bounds Showcase 2026"). Ayrıştırıcı bilerek muhafazakârdır: emin değilse
None döner. Çıkan oyun adı tek başına karar vermez; Steam'de aranıp "yakında" sayfası olup olmadığı doğrulanır.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- sözlükler
_TRAILER = re.compile(
    r"\b(trailer|teaser|reveal|announcement|announce|gameplay|cinematic|first look|world premiere|"
    r"release date|launch date|demo|playtest|alpha|beta|overview)\b", re.I,
)
# Başlığın fragman olduğunu gösteren çekirdek sözcükler ("gameplay" tek başına yetmez: oynanış kaydı olabilir).
# "trailer/teaser/cinematic" yeterlidir; diğerleri yalnızca kısa (≤5 sözcük) bir parçada sayılır
_STRONG = re.compile(r"\b(trailer|teaser|cinematic)\b", re.I)
_WEAK = re.compile(r"\b(reveal|announcement|release date|launch date|first look|world premiere)\b", re.I)

# Bunlar oyun fragmanı değil: inceleme, liste, röportaj, müzik, DLC/güncelleme, sosyal medya kırpıntısı
_REJECT_TITLE = re.compile(
    r"\b(review|reaction|reacts?|tier list|top \d+|best of|best \w+ (mods?|weapons?|builds?)|\d+ things|"
    r"everything (we know|you need)|explained|breakdown|analysis|interview|podcast|recap|how to|guide|tips|"
    r"walkthrough|let'?s play|unboxing|hands[- ]on|soundtrack|ost|visualizer|official audio|music video|dlc|"
    r"expansion|season \d+|update|patch notes|dev ?log|developer diary|behind the scenes|co-?stream|"
    r"live ?stream|intro video|roster)\b", re.I,
)
# Oyun adı yerine etkinlik/kampanya adı yakalandıysa (ör. "Steam Autumn Sale 2026", "Summer Game Fest")
_REJECT_GAME = re.compile(
    r"\b(sale|next fest|fest|showcase|direct|awards|edition|expo|summit|conference|presents?|steam deck|"
    r"day of the devs|summer game fest|state of play|pc gaming show|future games show|triple-i|ign live)\b|^steam\b",
    re.I,
)
# Segment oyun adı değil: etkinlik/pazarlama/tarih ifadeleri ("Launching on November 12", "Out of Bounds Showcase 2026")
_MARKETING = re.compile(
    r"^(launching|launches|coming|available|arrives?|releases?|playable|wishlist|official|exclusive|world premiere)\b|"
    r"\b(showcase|direct|fest|summer game fest|gamescom|tokyo game show|game awards|pc gaming show|state of play|"
    r"day of the devs|wholesome direct|future games show)\b|"
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b", re.I,
)
_LAUNCH = re.compile(r"\b(launch trailer|launch cinematic|release trailer|out now|available now|now available|1\.0 launch|"
                     r"is out|is now out|out on)\b", re.I)
_RELEASE_DATE = re.compile(r"\b(release date|launch date|launching|launches on|arrives on)\b", re.I)
_ANNOUNCE = re.compile(r"\b(announcement|announce|reveal|world premiere|first look)\b", re.I)
_DEMO = re.compile(r"\b(demo|playtest|beta|alpha)\b", re.I)
_GAMEPLAY = re.compile(r"\b(gameplay|overview)\b", re.I)
_TEASER = re.compile(r"\bteaser\b", re.I)

# Mağaza sayfası olmayan (konsol/Epic/yeni duyuru) fragmanlarda büyük markaları ayıklamak için
BIG_FRANCHISES = [
    "call of duty", "grand theft auto", "gta", "fifa", "ea sports", "battlefield", "star wars", "marvel", "warcraft",
    "final fantasy", "zelda", "mario", "pokemon", "pokémon", "gears of war", "halo", "assassin's creed",
    "elder scrolls", "fallout", "resident evil", "silent hill", "street fighter", "mortal kombat", "forza",
    "witcher", "cyberpunk", "god of war", "spider-man", "batman", "minecraft", "fortnite", "metal gear",
    "dragon age", "mass effect", "diablo", "overwatch", "starcraft", "tekken", "dark souls", "elden ring",
    "monster hunter", "kingdom hearts", "persona", "fire emblem", "xenoblade", "smash bros", "007", "need for speed",
    "modern warfare", "black ops", "warzone", "ghost recon", "rainbow six", "far cry", "tom clancy", "mortal kombat",
    "crash bandicoot", "tomb raider", "borderlands", "bioshock", "destiny", "apex legends", "valorant",
]

_BRACKETS = re.compile(r"[\[\(][^\]\)]{0,40}[\]\)]")
_SEPARATORS = re.compile(r"\s+[|–—•/]{1,2}\s+|\s+-\s+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


@dataclass(frozen=True)
class TrailerInfo:
    game: str
    kind: str  # announce | gameplay | release_date | launch | demo | teaser | trailer
    released: bool  # başlıktan oyunun çıktığı anlaşılıyor (launch trailer, out now)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, "fragman")


KIND_LABELS = {
    "announce": "duyuru fragmanı", "gameplay": "oynanış fragmanı", "release_date": "çıkış tarihi fragmanı",
    "launch": "çıkış fragmanı", "demo": "demo fragmanı", "teaser": "teaser", "trailer": "fragman",
}


def _kind(title: str) -> str:
    if _DEMO.search(title):
        return "demo"
    if _LAUNCH.search(title):
        return "launch"
    if _RELEASE_DATE.search(title):
        return "release_date"
    if _ANNOUNCE.search(title):
        return "announce"
    if _TEASER.search(title):
        return "teaser"
    if _GAMEPLAY.search(title):
        return "gameplay"
    return "trailer"


def _is_keyword_segment(seg: str) -> bool:
    return bool(_TRAILER.search(seg)) or bool(_MARKETING.search(seg))


def _has_trailer_signal(segments: list[str]) -> bool:
    return any(
        _STRONG.search(seg) or (_WEAK.search(seg) and len(seg.split()) <= 5) for seg in segments
    )


_CORE_WORDS = {"trailer", "teaser", "reveal", "announcement", "gameplay", "cinematic"}
_MODIFIERS = {
    "official", "new", "first", "final", "full", "story", "launch", "release", "date", "world", "premiere", "debut",
    "cinematic", "gameplay", "reveal", "announcement", "teaser", "extended", "short", "4k", "live-action", "live",
}


def _name_before_keywords(text: str) -> str:
    """"Moss Garden Official Reveal Trailer" -> "Moss Garden": ilk fragman sözcüğünden ve önündeki niteleyicilerden önceki kısım."""
    tokens = text.split()
    for i, tok in enumerate(tokens):
        if tok.lower().strip(".,:;!?()[]\"'") in _CORE_WORDS:
            j = i
            while j > 0 and tokens[j - 1].lower().strip(".,:;!?()[]\"'") in _MODIFIERS:
                j -= 1
            return " ".join(tokens[:j])
    return ""


def clean_game_name(name: str) -> str:
    name = _EMOJI.sub("", name)
    name = _BRACKETS.sub(" ", name)
    name = name.strip(" \t\"'“”‘’`.,:;-–—|")
    name = re.sub(r"\s+", " ", name)
    return name


def parse_trailer_title(title: str) -> TrailerInfo | None:
    """Başlık bir oyun fragmanıysa (oyun adı, tür) döner; değilse None."""
    raw = (title or "").strip()
    if not raw or "#" in raw or not _TRAILER.search(raw) or _REJECT_TITLE.search(raw):
        return None
    probe = _EMOJI.sub("", raw)
    # "[OFFICIAL TRAILER]" gibi köşeli ifadeler ayraç gibi davranır; diğer parantezli notlar ("(PC)") atılır
    probe = re.sub(
        r"[\[\(]([^\]\)]*)[\]\)]",
        lambda m: f" | {m.group(1)} | " if _TRAILER.search(m.group(1)) else " ",
        probe,
    )

    segments = [s.strip() for s in _SEPARATORS.split(probe) if s.strip(" |")]
    if not _has_trailer_signal(segments):
        return None
    game = ""
    for seg in segments:
        if not _is_keyword_segment(seg):
            game = seg
            break
    if not game:
        # "At Fate's End: Official Gameplay Trailer" ya da "Trailer: Game": iki noktadan böl
        for seg in segments:
            left, sep, right = seg.partition(": ")
            if sep and _is_keyword_segment(right) and not _is_keyword_segment(left) and len(right.split()) <= 6:
                game = left
                break
            if sep and _is_keyword_segment(left) and not _is_keyword_segment(right) and len(left.split()) <= 4:
                game = right
                break
    if not game and len(segments) == 1:
        game = _name_before_keywords(segments[0])

    game = clean_game_name(game)
    if not _valid_game_name(game) or _REJECT_GAME.search(game):
        return None
    kind = _kind(probe)
    return TrailerInfo(game=game, kind=kind, released=kind == "launch")


def _valid_game_name(name: str) -> bool:
    if not (2 <= len(name) <= 70):
        return False
    if len(name.split()) > 9:
        return False
    if _TRAILER.fullmatch(name.lower()) or name.lower() in {"official", "new", "gameplay", "trailer"}:
        return False
    # en az bir harf: yalnızca sayı/yıl bir oyun adı değildir ("2026")
    return bool(re.search(r"[A-Za-zÀ-ɏЀ-ӿ぀-ヿ一-鿿]", name))


def is_big_franchise(name: str) -> bool:
    n = name.lower()
    return any(re.search(rf"\b{re.escape(f)}\b", n) for f in BIG_FRANCHISES)


# --------------------------------------------------------------------------- ses getirme puanı
def _log_scale(x: float, floor: float, full: float) -> float:
    if x <= floor:
        return 0.0
    lo, hi = math.log10(1 + floor), math.log10(1 + full)
    return max(0.0, min(1.0, (math.log10(1 + x) - lo) / (hi - lo)))


def buzz_score(views: int, age_hours: float, velocity_per_hour: float | None = None) -> float:
    """Fragmanın ne kadar ses getirdiği (0..1): toplam izlenme, yaşa göre hız ve (varsa) son ölçümdeki hız.

    Ölçekler bilerek logaritmiktir: 2 bin izlenme ~0, 1 milyon ~1. Çok yeni videoda (1 saatten az) hız
    şişmesin diye yaş en az 1 saat alınır.
    """
    age = max(age_hours, 1.0)
    vph = views / age
    s_views = _log_scale(views, 2_000, 1_000_000)
    s_rate = _log_scale(vph, 50, 20_000)
    s_velocity = _log_scale(velocity_per_hour, 50, 20_000) if velocity_per_hour is not None else s_rate
    return round(0.4 * s_views + 0.3 * s_rate + 0.3 * s_velocity, 3)
