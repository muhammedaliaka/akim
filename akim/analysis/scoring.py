"""Momentum, Roblox doygunluğu ve nihai karar hesabı.

Karar formülü (her bileşen 0..1):
    fırsat = 0.25 * bağımsızlık + 0.45 * momentum + 0.30 * (1 - roblox_doygunluğu)

Kurallar:
  * bağımsızlık < eşik               -> ELENDİ (büyük marka)
  * Roblox klonları hızla büyüyor     -> AKIM BAŞLADI
  * Roblox doymuş                     -> DOYMUŞ
  * Roblox'ta yok/erken + fırsat yüksek + gerçek momentum -> FIRSAT
  * fırsat orta                        -> İZLE
  * Roblox doğrulanamıyor (erişim yok/kapalı) -> en fazla İZLE: "Roblox'ta yok" iddiası kanıtsız yapılmaz
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import ScoringConfig
from ..models import Decision, RobloxGame, RobloxStatus


@dataclass
class MomentumSignals:
    best_rank: int | None = None
    chart_count: int = 0
    max_climb: int = 0  # pozitif = sıralamada yükselmiş
    is_new_entry: bool = False
    ccu_now: int | None = None
    ccu_ratio: float | None = None  # şimdi / 24 saat önceki taban
    release_age_days: float | None = None
    discount_pct: int = 0
    top_n: int = 100


def momentum_score(s: MomentumSignals) -> tuple[float, list[str]]:
    """Oyunun şu an ne kadar "yükselişte" olduğu (0..1).

    Yalnızca listede durmak az puan getirir; asıl puan yeni giriş, sıra tırmanışı,
    oyuncu artışı ve yeni çıkış tarihinden gelir. Yıllardır listede duran "evergreen"
    oyunlar (ör. Rust, Among Us) ancak gerçek bir sıçrama yaparsa yüksek puan alır.
    """
    score = 0.0
    reasons: list[str] = []
    # Büyük indirimde sıra tırmanışı kampanya etkisidir, akım değil
    on_sale = s.discount_pct >= 30
    if s.best_rank:
        rank_part = 0.25 * max(0.0, 1 - (s.best_rank - 1) / max(1, s.top_n))
        score += rank_part * (0.7 if s.discount_pct >= 50 else 1.0)
        reasons.append(f"en iyi sıra #{s.best_rank}")
    if s.chart_count >= 3:
        score += 0.08
    elif s.chart_count == 2:
        score += 0.05
    if s.chart_count >= 2:
        reasons.append(f"{s.chart_count} listede birden")

    surge = 0.0
    old_game = s.release_age_days is not None and s.release_age_days > 365
    if s.is_new_entry:
        # Eski bir oyunun indirimle listeye girmesi kampanya etkisidir
        surge += 0.25 * (0.4 if on_sale and old_game else 1.0)
        reasons.append("listelere yeni girdi" + (f" (%{s.discount_pct} indirimde)" if on_sale and old_game else ""))
    if s.max_climb > 0:
        surge += 0.20 * min(1.0, s.max_climb / 20) * (0.3 if on_sale else 1.0)
        reasons.append(f"{s.max_climb} sıra yükseldi" + (f" (%{s.discount_pct} indirimde)" if on_sale else ""))
    if s.ccu_ratio is not None and s.ccu_ratio > 1.1:
        surge += 0.15 * min(1.0, s.ccu_ratio - 1.0)
        reasons.append(f"anlık oyuncu x{s.ccu_ratio:.1f}")
    score += surge

    age = s.release_age_days
    if age is not None and age >= 0:
        if age <= 14:
            score += 0.20
            reasons.append("son 2 haftada çıktı")
        elif age <= 45:
            score += 0.15
            reasons.append("yeni çıktı")
        elif age <= 120:
            score += 0.08
            reasons.append("son 4 ayda çıktı")
        elif age <= 365:
            score += 0.03
        # Eski oyun ve belirgin bir sıçrama yoksa: kalıcı popülerlik, akım değil
        if surge < 0.1:
            if age > 730:
                score *= 0.5
                reasons.append("eski oyun, yeni sıçrama yok")
            elif age > 365:
                score *= 0.75
    return round(min(1.0, score), 3), reasons


def _log_scale(x: float, floor: float, full: float) -> float:
    if x <= floor:
        return 0.0
    lo, hi = math.log10(1 + floor), math.log10(1 + full)
    return max(0.0, min(1.0, (math.log10(1 + x) - lo) / (hi - lo)))


@dataclass
class RobloxAssessment:
    status: RobloxStatus
    saturation: float
    match_count: int  # klon + benzer
    total_playing: int  # ağırlıklı toplam anlık oyuncu
    top_visits: int
    reasons: list[str] = field(default_factory=list)
    clone_count: int = 0
    similar_count: int = 0


def _saturation(games: list[RobloxGame], cfg: ScoringConfig) -> float:
    if not games:
        return 0.0
    return min(1.0, (
        0.30 * min(1.0, len(games) / max(1, cfg.saturation_match_count))
        + 0.45 * _log_scale(sum(g.playing for g in games), 10, cfg.saturation_ccu)
        + 0.25 * _log_scale(max(g.visits for g in games), 10_000, cfg.saturation_visits)
    ))


def assess_roblox(
    matches: list[RobloxGame | tuple[RobloxGame, float]], baseline_playing: int | None, cfg: ScoringConfig
) -> RobloxAssessment:
    """Roblox tarafının ne kadar dolu olduğunu ölçer.

    matches: eşleşen oyunlar; (oyun, ağırlık) çiftleri de olabilir. Ağırlığı 1 olanlar klon, daha düşük
    olanlar "benzer oynanış"tır. Doygunluk klonlardan hesaplanır; benzer oyunlar yalnızca sınırlı bir ek
    rekabet katkısı yapar (en fazla similar_weight): RIVALS gibi dev ama sadece aynı türdeki bir oyun,
    bir taktik nişancının Roblox'ta "zaten var" sayılmasına yetmez.
    total_playing yalnızca klonların anlık oyuncusudur; ani artış (AKIM BAŞLADI) bundan ölçülür, böylece
    dev bir türdaşın günlük dalgalanması sahte alarm üretmez.
    """
    items = [(m, 1.0) if isinstance(m, RobloxGame) else m for m in matches]
    items = [(g, w) for g, w in items if w > 0]
    clones = [g for g, w in items if w >= 0.99]
    similar = [g for g, w in items if w < 0.99]
    if not items:
        return RobloxAssessment(RobloxStatus.NONE, 0.0, 0, 0, 0, ["Roblox'ta benzer oyun yok"])
    sat_clone = _saturation(clones, cfg)
    sat_similar = _saturation(similar, cfg)
    sat = round(min(1.0, sat_clone + cfg.similar_weight * sat_similar * (1 - sat_clone)), 3)
    total_playing = sum(g.playing for g in clones)
    top_visits = max((g.visits for g in clones), default=0)
    reasons = [f"{len(clones)} klon, {len(similar)} benzer oynanış"]
    if clones:
        reasons.append(f"klonlarda toplam {total_playing} anlık oyuncu")
    if similar:
        big = max(similar, key=lambda g: g.playing)
        reasons.append(f"en büyük türdaş: {big.name[:40]} ({big.playing} oyuncu)")

    rising = (
        baseline_playing is not None
        and total_playing >= cfg.rising_min_playing
        and total_playing >= baseline_playing * cfg.rising_ratio
    )
    if rising:
        status = RobloxStatus.RISING
        reasons.append(f"24 saatte {baseline_playing} -> {total_playing} oyuncu")
    elif not clones and sat < 0.15:
        status = RobloxStatus.NONE  # birebir karşılık yok, yalnızca zayıf benzerler
    elif sat >= 0.7:
        status = RobloxStatus.SATURATED
    elif sat >= 0.4:
        status = RobloxStatus.COMPETITIVE
    else:
        status = RobloxStatus.EARLY
    return RobloxAssessment(status, sat, len(items), total_playing, top_visits, reasons, len(clones), len(similar))


def opportunity_score(indie: float, momentum: float, saturation: float) -> float:
    return round(0.25 * indie + 0.45 * momentum + 0.30 * (1 - saturation), 3)


def decide(
    indie: float, momentum: float, roblox: RobloxAssessment, cfg: ScoringConfig, min_indie: float
) -> tuple[Decision, float]:
    score = opportunity_score(indie, momentum, roblox.saturation)
    if indie < min_indie:
        return Decision.FILTERED, score
    if roblox.status == RobloxStatus.UNKNOWN:
        # Roblox tarafı bilinmiyor: doygunluk nötr (0.5) varsayılır ve FIRSAT/AKIM/DOYMUŞ kararı verilmez.
        # Roblox'a tekrar ulaşılınca oyun taranır ve karar kesinleşir.
        if score >= cfg.watch_threshold and momentum >= cfg.min_watch_momentum:
            return Decision.WATCH, score
        return Decision.LOW, score
    if roblox.status == RobloxStatus.RISING:
        return Decision.RISING_TREND, score
    if roblox.status == RobloxStatus.SATURATED:
        return Decision.SATURATED, score
    if (
        roblox.status in (RobloxStatus.NONE, RobloxStatus.EARLY)
        and score >= cfg.opportunity_threshold
        and momentum >= cfg.min_opportunity_momentum
    ):
        return Decision.OPPORTUNITY, score
    if score >= cfg.watch_threshold and momentum >= cfg.min_watch_momentum:
        return Decision.WATCH, score
    return Decision.LOW, score
