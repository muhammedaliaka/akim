"""Momentum, Roblox doygunluğu ve nihai karar hesabı.

Karar formülü (her bileşen 0..1):
    fırsat = 0.25 * bağımsızlık + 0.45 * momentum + 0.30 * (1 - roblox_doygunluğu)

Kurallar:
  * bağımsızlık < eşik               -> ELENDİ (büyük marka)
  * Roblox klonları hızla büyüyor     -> AKIM BAŞLADI
  * Roblox doymuş                     -> DOYMUŞ
  * Roblox'ta yok/erken + fırsat yüksek + gerçek momentum -> FIRSAT
  * fırsat orta                        -> İZLE
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
    match_count: int
    total_playing: int
    top_visits: int
    reasons: list[str] = field(default_factory=list)


def assess_roblox(
    matches: list[RobloxGame], baseline_playing: int | None, cfg: ScoringConfig
) -> RobloxAssessment:
    """matches: eşik üstü benzerlikteki Roblox oyunları. baseline_playing: son 24 saatteki en düşük toplam."""
    if not matches:
        return RobloxAssessment(RobloxStatus.NONE, 0.0, 0, 0, 0, ["Roblox'ta eşleşen oyun yok"])
    n = len(matches)
    total_playing = sum(g.playing for g in matches)
    top_visits = max(g.visits for g in matches)
    sat = (
        0.30 * min(1.0, n / max(1, cfg.saturation_match_count))
        + 0.45 * _log_scale(total_playing, 10, cfg.saturation_ccu)
        + 0.25 * _log_scale(top_visits, 10_000, cfg.saturation_visits)
    )
    sat = round(min(1.0, sat), 3)
    reasons = [f"{n} benzer oyun", f"toplam {total_playing} anlık oyuncu", f"en çok ziyaret {top_visits:,}"]

    rising = (
        baseline_playing is not None
        and total_playing >= cfg.rising_min_playing
        and total_playing >= baseline_playing * cfg.rising_ratio
    )
    if rising:
        status = RobloxStatus.RISING
        reasons.append(f"24 saatte {baseline_playing} -> {total_playing} oyuncu")
    elif sat >= 0.7:
        status = RobloxStatus.SATURATED
    elif sat >= 0.4:
        status = RobloxStatus.COMPETITIVE
    else:
        status = RobloxStatus.EARLY
    return RobloxAssessment(status, sat, n, total_playing, top_visits, reasons)


def opportunity_score(indie: float, momentum: float, saturation: float) -> float:
    return round(0.25 * indie + 0.45 * momentum + 0.30 * (1 - saturation), 3)


def decide(
    indie: float, momentum: float, roblox: RobloxAssessment, cfg: ScoringConfig, min_indie: float
) -> tuple[Decision, float]:
    score = opportunity_score(indie, momentum, roblox.saturation)
    if indie < min_indie:
        return Decision.FILTERED, score
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
