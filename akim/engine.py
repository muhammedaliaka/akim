"""Akım motoru: veri toplama döngüleri, analiz, karar ve bildirim üretimi.

Döngüler (hepsi aynı anda, birbirini bloklamadan çalışır):
  * steam / epic      : listeleri çeker, sıra değişimlerini ve yeni girişleri yakalar
  * roblox tarayıcı   : takip edilen bağımsız oyunları Roblox'ta arar (önce yeni/sinyalli olanlar)
  * klon takibi       : bilinen Roblox klonlarının anlık oyuncu sayısını izler (akım Roblox'a geçti mi?)
  * ccu               : Steam oyunlarının anlık oyuncu sayısını izler (ani sıçrama)
  * özet              : periyodik fırsat özeti
  * bakım             : eski verileri temizler
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass, field

from .analysis.concepts import CandidateText
from .analysis.indie import IndieClassifier
from .analysis.laya_judge import LayaJudge
from .analysis.matching import match_score, search_queries, title_key
from .analysis.similarity import Evidence, GameContext, classify, heuristic_evidence
from .analysis.scoring import MomentumSignals, RobloxAssessment, assess_roblox, decide, momentum_score
from .config import Config
from .events import EventBus
from .http import HttpClient
from .models import Alert, ChartEntry, Decision, Priority, RobloxGame, RobloxStatus
from .sources import EpicSource, RobloxSource, SteamSource
from .storage import Storage

log = logging.getLogger(__name__)

HOUR = 3600
DAY = 86400
BOOTSTRAP_MAX_SECONDS = 30 * 60

CHART_LABELS = {
    "steam_topsellers": "Steam En Çok Satanlar",
    "steam_weekly": "Steam Haftalık Satış",
    "steam_mostplayed": "Steam En Çok Oynanan",
    "epic_top-sellers": "Epic En Çok Satanlar",
    "epic_most-played": "Epic En Çok Oynanan",
    "epic_trending": "Epic Trend",
    "epic_most-popular": "Epic Popüler",
    "epic_top-new-releases": "Epic Yeni Çıkanlar",
    "epic_top-wishlisted": "Epic İstek Listesi",
    "epic_top-player-reviewed": "Epic En Beğenilen",
    "epic_free": "Epic Ücretsiz",
}
DECISION_EMOJI = {
    Decision.OPPORTUNITY: "🟢",
    Decision.RISING_TREND: "🚀",
    Decision.WATCH: "🟡",
    Decision.SATURATED: "🔴",
    Decision.LOW: "⚪",
    Decision.FILTERED: "⚫",
}
ACTIONABLE = {Decision.OPPORTUNITY, Decision.RISING_TREND, Decision.WATCH}
# Steam'de oyun olmayan araç/yazılım etiketleri
NON_GAME_TAGS = {
    "Utilities", "Software", "Design & Illustration", "Animation & Modeling", "Video Production",
    "Audio Production", "Photo Editing", "Web Publishing", "Game Development", "Software Training",
}


def chart_label(chart: str) -> str:
    return CHART_LABELS.get(chart, chart)


@dataclass
class Signal:
    type: str  # chart_entry | rank_surge | ccu_surge
    text: str


@dataclass
class RobloxEvents:
    first_clone: bool = False
    new_clones: list[dict] = field(default_factory=list)


@dataclass
class LookupResult:
    title: str
    queries: list[str]
    generic: bool
    matches: list[tuple[RobloxGame, Evidence]]
    related: list[tuple[RobloxGame, Evidence]]

    @property
    def all_games(self) -> list[RobloxGame]:
        return [g for g, _ in self.matches + self.related]


def _fmt_int(n: int | float | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


class Engine:
    def __init__(self, cfg: Config, store: Storage, http: HttpClient, bus: EventBus):
        self.cfg = cfg
        self.store = store
        self.http = http
        self.bus = bus
        g = cfg.general
        self.steam = SteamSource(http, country=g.country, language=g.language, top_n=cfg.steam.top_n)
        self.epic = EpicSource(http, country=g.country)
        self.roblox = RobloxSource(http, request_delay=cfg.roblox.request_delay_seconds)
        http.set_host_interval("api.steampowered.com", 0.25)
        self.indie = IndieClassifier(
            extra_major=cfg.filters.extra_major_publishers,
            extra_midtier=cfg.filters.extra_midtier_publishers,
            allow=cfg.filters.allow_publishers,
        )
        self._ignore = [re.compile(p) for p in cfg.filters.ignore_titles]
        self.laya = LayaJudge(cfg.laya, store)
        self.notifier = None  # __main__ tarafından atanır (kanallar engine.command'a ihtiyaç duyar)
        self._pending: dict[str, list[Signal]] = {}
        # title_key -> (zaman, sorgulayan oyun anahtarı, sonuç)
        self._lookup_cache: dict[str, tuple[float, str, LookupResult]] = {}
        self._wake_scanner = asyncio.Event()
        self._charts_polled: set[str] = set()
        self.bootstrapping = False
        self.started_at = time.time()
        self.last_run: dict[str, float] = {}
        if not self.store.get_kv("first_run_ts"):
            self.store.set_kv("first_run_ts", str(self.started_at))
        self.first_run_ts = float(self.store.get_kv("first_run_ts"))

    # ------------------------------------------------------------------ zaman pencereleri
    @property
    def min_indie(self) -> float:
        return self.cfg.filters.min_indie_score

    def track_since(self, now: float | None = None) -> float:
        return (now or time.time()) - self.cfg.general.track_days * DAY

    def fresh_since(self, now: float | None = None) -> float:
        """Bu zamandan sonra görülen liste sıraları 'şu an listede' sayılır."""
        longest = max(self.cfg.steam.interval_minutes, self.cfg.epic.interval_minutes)
        return (now or time.time()) - longest * 60 * 2.5

    def _ignored(self, game) -> bool:
        if game.kind != "game" or NON_GAME_TAGS.intersection(game.tags[:8]):
            return True
        return any(p.search(game.title) for p in self._ignore)

    # ------------------------------------------------------------------ liste toplama
    async def poll_steam(self) -> None:
        entries, errors = await self.steam.fetch_charts(self.cfg.steam.charts)
        await self.ingest("steam", entries, errors)

    async def poll_epic(self) -> None:
        entries, errors = await self.epic.fetch_charts(self.cfg.epic.collections, self.cfg.epic.include_free_games)
        await self.ingest("epic", entries, errors)

    async def ingest(self, source: str, entries: list[ChartEntry], errors: dict[str, str]) -> None:
        now = time.time()
        if entries:
            if self.store.source_ok(source, now):
                await self._system_alert(f"{source.capitalize()} kaynağı yeniden çalışıyor", "Veri akışı normale döndü.", Priority.LOW)
        if errors:
            msg = "; ".join(f"{k}: {v}" for k, v in errors.items())
            if not entries:
                await self._source_failure(source, msg)
            else:
                log.warning("%s kısmi hata: %s", source, msg)
        if not entries:
            return

        gap = (self.cfg.steam.interval_minutes if source == "steam" else self.cfg.epic.interval_minutes) * 60 * 2.5
        touched: set[str] = set()
        by_chart: dict[str, list[ChartEntry]] = {}
        seen: set[tuple[str, str]] = set()
        for e in sorted(entries, key=lambda x: (x.chart, x.rank)):
            if (e.chart, e.game.key) in seen:  # aynı oyun aynı listede iki kez: en iyi sırayı tut
                continue
            seen.add((e.chart, e.game.key))
            by_chart.setdefault(e.chart, []).append(e)

        for chart, items in by_chart.items():
            prev_state = self.store.chart_state(chart)
            initialized = bool(prev_state)
            for e in items:
                g = e.game
                if self._ignored(g):
                    continue
                self.store.upsert_game(g, self.indie.assess(g), now)
                prev = prev_state.get(g.key)
                self.store.record_rank(e, prev, now)
                if e.metric is not None and chart == "steam_mostplayed":
                    self.store.add_metric(g.key, "ccu", e.metric, now)
                stored = self.store.get_game(g.key)
                if not stored or (stored["indie_score"] or 0) < self.min_indie:
                    continue
                touched.add(g.key)
                if not initialized:
                    continue  # listenin ilk kaydı: herkes "yeni" görünür, sinyal üretme
                if prev is None or prev["last_seen"] < now - gap:
                    self._add_signal(g.key, Signal("chart_entry", f"{chart_label(chart)} listesine #{e.rank} sırasından girdi"))
                elif prev["rank"] - e.rank >= self.cfg.scoring.rank_surge_positions:
                    self._add_signal(g.key, Signal("rank_surge", f"{chart_label(chart)}: #{prev['rank']} → #{e.rank}"))

        self._charts_polled.add(source)
        log.info("%s: %d liste girdisi, %d bağımsız aday", source, len(entries), len(touched))
        for key in touched:
            if self.store.last_roblox_check(key):
                await self.evaluate(key)
        self._wake_scanner.set()
        self.bus.publish("charts", {"source": source, "entries": len(entries), "candidates": len(touched), "ts": now})

    def _add_signal(self, key: str, signal: Signal) -> None:
        bucket = self._pending.setdefault(key, [])
        if all(s.type != signal.type for s in bucket):
            bucket.append(signal)

    # ------------------------------------------------------------------ roblox
    async def context_for(self, game: dict) -> GameContext:
        """Mağaza oyununun oynanış bağlamı. Etiketi olmayan Epic oyunları Steam'deki karşılığından beslenir."""
        tags, desc = list(game.get("tags") or []), game.get("description") or ""
        if game.get("store") == "epic" and not tags:
            try:
                twin = await self.steam.find_by_title(game["title"])
            except Exception as exc:
                log.debug("Steam karşılığı bulunamadı (%s): %s", game["title"], exc)
                twin = None
            if twin and twin.tags:
                tags = twin.tags
                if len(twin.description) > len(desc):
                    desc = twin.description
                self.store.enrich_game(game["key"], tags, desc)
        return GameContext(game["title"], desc, tags)

    async def context_for_title(self, title: str) -> tuple[GameContext, str]:
        """Serbest başlık için bağlam (anlık kontrol). Önce takip edilen oyunlar, sonra Steam. (bağlam, kaynak)"""
        row = self.store._one(
            "SELECT key FROM games WHERE title_key=? ORDER BY length(COALESCE(description,'')) DESC LIMIT 1",
            (title_key(title),),
        )
        if row:
            game = self.store.get_game(row["key"])
            if game and (game["description"] or game["tags"]):
                return await self.context_for(game), f"takip edilen oyun ({game['store'].capitalize()})"
        try:
            twin = await self.steam.find_by_title(title)
        except Exception:
            twin = None
        if twin:
            return GameContext(twin.title, twin.description, twin.tags), f"Steam: {twin.title}"
        return GameContext(title), "yalnızca isim (Steam'de bulunamadı)"

    async def lookup_roblox(self, ctx: GameContext) -> LookupResult:
        """Roblox'ta arar; her adayı isim, açıklama-atfı, oynanış ve (açıksa) Laya ile değerlendirir."""
        rc, sc = self.cfg.roblox, self.cfg.scoring
        queries = search_queries(ctx.title, rc.max_queries_per_game)
        found: dict[int, RobloxGame] = {}
        want_details: list[int] = []
        for q in queries:
            for pos, g in enumerate(await self.roblox.search(q)):
                if g.universe_id in found:
                    continue
                found[g.universe_id] = g
                # Detay (ziyaret, tür, tam açıklama) umut vadeden adaylar için: aramanın ilk sıraları,
                # açıklaması boş gelenler ve isim/atıf ön puanı yüksek olanlar
                pre, _ = match_score(ctx.title_profile, g.name, g.description)
                if pre >= 0.5 or pos < 15 or (not g.description and pos < 25):
                    want_details.append(g.universe_id)
        if want_details:
            details = await self.roblox.details(want_details[:100])
            for uid, d in details.items():
                s = found[uid]
                d.up_votes, d.down_votes = s.up_votes, s.down_votes
                d.description = d.description or s.description
                found[uid] = d

        games = list(found.values())  # Roblox arama sırasıyla
        evidences = heuristic_evidence(ctx, [CandidateText(g.name, g.description, g.genre) for g in games])
        has_ctx = not ctx.concept_profile.empty
        lc = self.cfg.laya
        laya_kw = {"laya_mode": lc.mode, "laya_reliability": lc.reliability if self.laya.available else 0.0}

        def reclassify() -> None:
            for g, ev in zip(games, evidences):
                classify(ev, sc, has_context=has_ctx, has_description=bool(g.description), **laya_kw)

        reclassify()

        if self.laya.available:
            # Yarısı sezgisel en iyiler, yarısı aramanın üst sıraları: sözlükte olmayan yeni mekaniklerde
            # sezgisel skor sıfır kalabilir; Laya'nın bu adayları da görmesi gerekir.
            half = max(1, lc.max_candidates // 2)
            by_score = [g for g, ev in sorted(zip(games, evidences), key=lambda x: -x[1].score) if ev.score >= lc.min_prescore]
            ask = list({g.universe_id: g for g in by_score[:half] + games[: lc.max_candidates]}.values())
            ask = ask[: lc.max_candidates]
            verdicts = await self.laya.judge(ctx, ask)
            for g, ev in zip(games, evidences):
                v = verdicts.get(g.universe_id)
                if v:
                    ev.laya, ev.laya_relation = v.similarity, v.relation
            reclassify()

        pairs = list(zip(games, evidences))
        matches = sorted(
            (p for p in pairs if p[1].kind != "none"),
            key=lambda p: (p[1].kind != "clone", -p[1].score, -p[0].playing),
        )[: rc.max_matches_per_game]
        # Eşleşme sayılmayan ama en yakın adaylar (ör. farklı isimli konsept benzerleri) panelde gösterilir
        related = sorted((p for p in pairs if p[1].kind == "none"), key=lambda p: -p[1].score)[:6]
        return LookupResult(ctx.title, queries, ctx.generic, matches, related)

    async def scan_roblox(self, key: str) -> None:
        game = self.store.get_game(key)
        if not game:
            return
        # Aynı oyun iki mağazada listelenmişse (Steam + Epic) Roblox'u iki kez sorgulama.
        # Aynı kaydın yeniden taranması ise her zaman taze arama yapar.
        tkey = game.get("title_key") or key
        cached = self._lookup_cache.get(tkey)
        if cached and cached[1] != key and time.time() - cached[0] < 30 * 60:
            result = cached[2]
        else:
            result = await self.lookup_roblox(await self.context_for(game))
            self._lookup_cache[tkey] = (time.time(), key, result)
            if len(self._lookup_cache) > 500:
                oldest = sorted(self._lookup_cache.items(), key=lambda kv: kv[1][0])[:100]
                for k, _ in oldest:
                    self._lookup_cache.pop(k, None)
        now = time.time()
        self.store.upsert_roblox_games(result.all_games, now)
        prev_check = self.store.last_roblox_check(key)
        new_ids = self.store.set_links(
            key,
            [
                {"universe_id": g.universe_id, "similarity": ev.score, "reason": ev.reason(), "kind": ev.kind,
                 "weight": ev.weight, "evidence": ev.to_dict()}
                for g, ev in result.matches
            ],
            now,
        )
        baseline = self.store.roblox_baseline(key, now - DAY, now)
        assessment = assess_roblox([(g, ev.weight) for g, ev in result.matches], baseline, self.cfg.scoring)
        related = [
            {"universe_id": g.universe_id, "name": g.name, "playing": g.playing, "visits": g.visits,
             "url": g.url, "similarity": ev.score, "reason": ev.reason()}
            for g, ev in result.related
        ]
        self.store.add_roblox_check(
            key, now, assessment.status.value, assessment.saturation, assessment.match_count,
            assessment.total_playing, assessment.top_visits, {"generic": result.generic, "related": related}, True,
            clone_count=assessment.clone_count,
        )
        events = RobloxEvents()
        if prev_check is not None:
            prev_clones = prev_check["clone_count"] if prev_check["clone_count"] is not None else prev_check["match_count"]
            events.first_clone = prev_clones == 0 and assessment.clone_count > 0
            events.new_clones = [
                {"name": g.name, "playing": g.playing, "url": g.url, "reason": ev.reason()}
                for g, ev in result.matches if g.universe_id in new_ids and ev.kind == "clone"
            ]
        log.info(
            "Roblox tarandı: %s -> %s (%d klon, %d benzer, %d oyuncu)", game["title"], assessment.status.label,
            assessment.clone_count, assessment.similar_count, assessment.total_playing,
        )
        await self.evaluate(key, roblox_events=events, scanned_at=now)

    def _due_games(self) -> list[dict]:
        now = time.time()
        rc = self.cfg.roblox
        rows = self.store.games_due_for_roblox(
            now, self.track_since(now), self.min_indie, rc.recheck_hours * HOUR, rc.hot_recheck_hours * HOUR
        )
        # Sinyali olan (yeni giren / hızla yükselen) oyunlar öne
        return sorted(rows, key=lambda r: r["key"] not in self._pending)

    async def refresh_clones(self) -> None:
        """Bilinen Roblox klonlarının anlık oyuncu sayılarını topluca yeniler (arama yapmadan)."""
        now = time.time()
        keys = [g["key"] for g in self.store.tracked_games(self.track_since(now), self.min_indie)]
        links = self.store.keys_with_links(keys)
        ids = sorted({u for us in links.values() for u, _ in us})
        if not ids:
            return
        details = await self.roblox.details(ids)
        self.store.upsert_roblox_games(details.values(), now)
        for key, uids in links.items():
            games = [(details[u], w) for u, w in uids if u in details]
            if not games:
                continue
            baseline = self.store.roblox_baseline(key, now - DAY, now)
            a = assess_roblox(games, baseline, self.cfg.scoring)
            self.store.add_roblox_check(
                key, now, a.status.value, a.saturation, a.match_count, a.total_playing, a.top_visits, None, False,
                clone_count=a.clone_count,
            )
            await self.evaluate(key)
        log.info("Roblox klon takibi: %d oyun, %d klon güncellendi", len(links), len(details))

    # ------------------------------------------------------------------ steam ccu
    async def poll_ccu(self) -> None:
        now = time.time()
        fresh = self.fresh_since(now)
        count = 0
        for g in self.store.tracked_games(self.track_since(now), self.min_indie):
            if g["store"] != "steam":
                continue
            last = self.store.last_metric(g["key"], "ccu")
            if last and last["ts"] >= fresh - 60 and last["ts"] > now - self.cfg.steam.ccu_interval_minutes * 60 * 0.8:
                ccu = int(last["value"])  # most played listesinden yeni gelmiş
            else:
                ccu = await self.steam.current_players(int(g["store_id"]))
                if ccu is None:
                    continue
                self.store.add_metric(g["key"], "ccu", ccu, now)
                count += 1
            ratio = self._ccu_ratio(g["key"], ccu, now)
            sc = self.cfg.scoring
            if ratio and ratio >= sc.ccu_surge_ratio and ccu >= sc.ccu_surge_min_players:
                self._add_signal(g["key"], Signal("ccu_surge", f"anlık oyuncu 24 saatte x{ratio:.1f} → {_fmt_int(ccu)}"))
                if self.store.last_roblox_check(g["key"]):
                    await self.evaluate(g["key"])
        log.info("Steam anlık oyuncu: %d oyun ölçüldü", count)

    def _ccu_ratio(self, key: str, ccu_now: float, now: float) -> float | None:
        series = self.store.metric_series(key, "ccu", now - 30 * HOUR)
        older = [p["value"] for p in series if p["ts"] <= now - 12 * HOUR]
        if not older:
            return None
        base = min(older)
        return round(ccu_now / base, 2) if base >= 50 else None

    # ------------------------------------------------------------------ değerlendirme
    def momentum_for(self, key: str, game: dict, now: float) -> tuple[float, list[str], MomentumSignals]:
        ranks = self.store.active_ranks(key, self.fresh_since(now))
        history = self.store.rank_history(key, now - DAY)
        s = MomentumSignals(top_n=self.cfg.steam.top_n)
        if ranks:
            s.best_rank = min(r["rank"] for r in ranks)
            s.chart_count = len(ranks)
        for r in ranks:
            oldest = next((h for h in history if h["chart"] == r["chart"]), None)
            climb = (oldest["rank"] - r["rank"]) if oldest else 0
            if r.get("source_prev_rank"):
                climb = max(climb, r["source_prev_rank"] - r["rank"])
            elif r["chart"] == "steam_weekly":
                s.is_new_entry = True  # haftalık listeye bu hafta ilk kez girdi
            s.max_climb = max(s.max_climb, climb)
        first = self.store.first_chart_seen(key)
        if first and first >= max(now - 3 * DAY, self.first_run_ts + HOUR):
            s.is_new_entry = True
        last_ccu = self.store.last_metric(key, "ccu")
        if last_ccu:
            s.ccu_now = int(last_ccu["value"])
            s.ccu_ratio = self._ccu_ratio(key, last_ccu["value"], now)
        if game.get("release_ts"):
            s.release_age_days = (now - game["release_ts"]) / DAY
        s.discount_pct = int(game.get("discount_pct") or 0)
        score, reasons = momentum_score(s)
        return score, reasons, s

    async def evaluate(
        self, key: str, *, roblox_events: RobloxEvents | None = None, scanned_at: float | None = None
    ) -> Decision | None:
        game = self.store.get_game(key)
        check = self.store.last_roblox_check(key)
        if not game or not check:
            return None
        now = time.time()
        momentum, m_reasons, m = self.momentum_for(key, game, now)
        full = self.store.last_roblox_check(key, full_scan_only=True)
        clones = check["clone_count"] if check["clone_count"] is not None else check["match_count"]
        assessment = RobloxAssessment(
            RobloxStatus(check["status"]), check["saturation"], check["match_count"],
            check["total_playing"], check["top_visits"], clone_count=clones,
            similar_count=max(0, check["match_count"] - clones),
        )
        decision, score = decide(game["indie_score"], momentum, assessment, self.cfg.scoring, self.min_indie)
        prev = self.store.get_decision(key)

        # Histerezis: eşiğin hemen altına düşen fırsat için bildirim dalgalanmasını önle
        if (
            prev and prev["decision"] == Decision.OPPORTUNITY.value and decision == Decision.WATCH
            and assessment.status in (RobloxStatus.NONE, RobloxStatus.EARLY)
            and score >= self.cfg.scoring.opportunity_threshold - 0.05
            and momentum >= self.cfg.scoring.min_opportunity_momentum - 0.05
        ):
            decision = Decision.OPPORTUNITY

        reasons = list(game["indie_reasons"][:2]) + m_reasons + [assessment.status.label]
        generic = False
        if full and full["related"]:
            generic = bool(json.loads(full["related"]).get("generic"))
        if generic:
            reasons.append("genel isimli oyun: eşleşmeleri elle doğrula")
        self.store.save_decision(
            key,
            {
                "decision": decision.value, "score": score, "indie": game["indie_score"], "momentum": momentum,
                "saturation": assessment.saturation, "roblox_status": assessment.status.value,
                "reasons": reasons, "last_roblox_scan": scanned_at,
            },
            now,
        )
        self.bus.publish("decision", {"key": key, "title": game["title"], "decision": decision.value, "score": score})

        signals = self._pending.pop(key, [])
        alerts = self._compose_alerts(game, prev, decision, score, momentum, m, assessment, signals, roblox_events or RobloxEvents())
        if self.bootstrapping:
            return decision
        for alert in alerts:
            await self.dispatch(alert)
        return decision

    def _fields(self, game: dict, score: float, momentum: float, m: MomentumSignals, a: RobloxAssessment) -> list[tuple[str, str]]:
        ranks = self.store.active_ranks(game["key"], self.fresh_since())
        chart_txt = ", ".join(f"{chart_label(r['chart'])} #{r['rank']}" for r in sorted(ranks, key=lambda r: r["rank"])[:3])
        fields = [
            ("Listeler", chart_txt or "-"),
            ("Yayıncı", ", ".join(game["publishers"][:2]) or "-"),
            ("Roblox", f"{a.status.label} ({a.clone_count} klon, {a.similar_count} benzer oynanış, "
                       f"{_fmt_int(a.total_playing)} oyuncu)"),
            ("Skor", f"{score:.2f} (bağımsızlık {game['indie_score']:.2f} · momentum {momentum:.2f} · doygunluk {a.saturation:.2f})"),
        ]
        if m.ccu_now:
            fields.append(("Steam anlık oyuncu", _fmt_int(m.ccu_now) + (f" (x{m.ccu_ratio:.1f})" if m.ccu_ratio else "")))
        clones = self.store.linked_roblox_games(game["key"])[:3]
        if clones:
            fields.append(("Öne çıkan Roblox benzerleri", "; ".join(
                f"{c['name'][:40]} (%{round((c.get('similarity') or 0) * 100)}, {_fmt_int(c['playing'])} oyuncu)"
                for c in clones)))
        return fields

    def _compose_alerts(
        self, game: dict, prev: dict | None, decision: Decision, score: float, momentum: float,
        m: MomentumSignals, a: RobloxAssessment, signals: list[Signal], rev: RobloxEvents,
    ) -> list[Alert]:
        if decision == Decision.FILTERED:
            return []
        title = game["title"]
        became = prev is None or prev["decision"] != decision.value
        fields = self._fields(game, score, momentum, m, a)
        base = dict(
            game_key=game["key"], url=game["url"], image=game["image"], fields=fields,
            dedupe_key=f"title:{game.get('title_key') or game['key']}",
        )
        out: list[Alert] = []

        if decision == Decision.OPPORTUNITY and became:
            critical = a.status == RobloxStatus.NONE and momentum >= 0.6
            roblox_txt = (
                "Roblox'ta henüz karşılığı YOK." if a.status == RobloxStatus.NONE
                else f"Roblox'ta yalnızca {a.clone_count} küçük klon ve {a.similar_count} benzer oynanışlı oyun var "
                f"({_fmt_int(a.total_playing)} anlık oyuncu)."
            )
            signal_txt = ("\nSinyal: " + "; ".join(s.text for s in signals)) if signals else ""
            out.append(Alert(
                type="opportunity", priority=Priority.CRITICAL if critical else Priority.HIGH,
                title=f"FIRSAT: {title}",
                body=f"{game['store'].capitalize()} listelerinde yükselen bağımsız oyun. {roblox_txt} "
                     f"İlk hamleyi yapmak için uygun zaman.{signal_txt}",
                **base,
            ))
        elif decision == Decision.RISING_TREND and became:
            out.append(Alert(
                type="rising_trend", priority=Priority.HIGH,
                title=f"AKIM BAŞLADI: {title} Roblox'ta hızla büyüyor",
                body=f"Roblox'taki benzer oyunların toplam anlık oyuncusu {_fmt_int(a.total_playing)} seviyesine çıktı. "
                     "Akım Roblox'a geçti; hızlı hareket etmek gerekiyor.",
                **base,
            ))

        if rev.first_clone:
            names = "; ".join(f"{c['name'][:50]} ({_fmt_int(c['playing'])} oyuncu)" for c in rev.new_clones[:3])
            out.append(Alert(
                type="first_clone", priority=Priority.HIGH,
                title=f"İlk Roblox klonu çıktı: {title}",
                body=f"Daha önce Roblox'ta karşılığı olmayan oyunun ilk benzeri yayında: {names or '-'}. "
                     "Yarış başladı.",
                **base,
            ))
        elif rev.new_clones and decision in ACTIONABLE:
            names = "; ".join(c["name"][:50] for c in rev.new_clones[:3])
            out.append(Alert(
                type="new_clone", priority=Priority.LOW,
                title=f"Yeni Roblox benzeri: {title}", body=f"Yeni eşleşen Roblox oyunları: {names}", **base,
            ))

        if decision == Decision.SATURATED and prev and prev["decision"] in {d.value for d in ACTIONABLE}:
            out.append(Alert(
                type="saturated", priority=Priority.LOW,
                title=f"Fırsat penceresi kapandı: {title}",
                body=f"Roblox tarafı doydu ({a.clone_count} klon, {a.similar_count} benzer oynanış, "
                     f"{_fmt_int(a.total_playing)} anlık oyuncu).",
                **base,
            ))

        if decision in ACTIONABLE and not any(x.type in ("opportunity", "rising_trend") for x in out):
            for s in signals:
                pr = Priority.HIGH if decision != Decision.WATCH and s.type != "chart_entry" else Priority.MEDIUM
                out.append(Alert(
                    type=s.type, priority=pr,
                    title=f"{DECISION_EMOJI[decision]} {title}: {s.text}",
                    body=f"Durum: {decision.label} · {a.status.label}.",
                    **base,
                ))
        return out

    async def dispatch(self, alert: Alert, force: bool = False) -> bool:
        if self.notifier is None:
            log.info("[bildirim] %s", alert.title)
            return False
        cooldown = 0 if alert.type in ("digest", "system", "bootstrap") else None
        return await self.notifier.dispatch(alert, cooldown_hours=cooldown, force=force)

    # ------------------------------------------------------------------ özetler / sistem
    def board(self, decisions: list[str] | None = None, limit: int = 100) -> list[dict]:
        """Karar tablosu; aynı oyunun Steam ve Epic kayıtları tek satırda birleştirilir."""
        now = time.time()
        rows = self.store.board(decisions, limit * 2, since=self.track_since(now), fresh_since=self.fresh_since(now))
        out: dict[str, dict] = {}
        for r in rows:  # satırlar önem sırasına göre geliyor; ilk görülen en iyisidir
            k = r.get("title_key") or r["key"]
            if k in out:
                out[k].setdefault("also", []).append({"key": r["key"], "store": r["store"], "url": r["url"],
                                                      "best_rank": r.get("best_rank"), "charts": r.get("charts")})
                continue
            out[k] = r
        return list(out.values())[:limit]

    def _summary_lines(self, rows: list[dict]) -> list[str]:
        lines = []
        for i, r in enumerate(rows, 1):
            d = Decision(r["decision"])
            rank = f"#{r['best_rank']}" if r.get("best_rank") else "-"
            status = RobloxStatus(r["roblox_status"]).label
            lines.append(f"{i}. {DECISION_EMOJI[d]} {r['title']} ({r['store'].capitalize()} {rank}) — {status} — {r['score']:.2f}")
        return lines

    async def send_digest(self, *, title: str | None = None, type_: str = "digest", priority: Priority = Priority.MEDIUM) -> bool:
        rows = self.board([Decision.OPPORTUNITY.value, Decision.RISING_TREND.value, Decision.WATCH.value],
                          limit=self.cfg.notifications.digest_size)
        if not rows:
            return False
        counts = {d: sum(1 for r in rows if r["decision"] == d.value) for d in ACTIONABLE}
        head = title or (
            f"Akım özeti: {counts[Decision.OPPORTUNITY]} fırsat, {counts[Decision.RISING_TREND]} akım, "
            f"{counts[Decision.WATCH]} izlemede"
        )
        await self.dispatch(Alert(type=type_, priority=priority, title=head, body="\n".join(self._summary_lines(rows))))
        return True

    async def _finish_bootstrap(self) -> None:
        self.bootstrapping = False
        self._pending.clear()
        log.info("Başlangıç taraması tamamlandı")
        sent = await self.send_digest(
            title="Akım çalışıyor — başlangıç taraması tamamlandı", type_="bootstrap", priority=Priority.HIGH
        )
        if not sent:
            await self.dispatch(Alert(
                type="bootstrap", priority=Priority.HIGH, title="Akım çalışıyor",
                body="Başlangıç taraması tamamlandı. Şu an eşik üstü fırsat yok; değişiklikler anlık bildirilecek.",
            ))

    async def _system_alert(self, title: str, body: str, priority: Priority) -> None:
        await self.dispatch(Alert(type="system", priority=priority, title=title, body=body))

    async def _source_failure(self, source: str, error: str) -> None:
        row = self.store.source_failed(source, error, time.time())
        log.error("%s kaynağı başarısız (%d. kez): %s", source, row["consecutive_failures"], error)
        if row["consecutive_failures"] >= 3 and not row["alerted"]:
            self.store.mark_source_alerted(source)
            await self._system_alert(
                f"{source.capitalize()} kaynağına ulaşılamıyor",
                f"Art arda {row['consecutive_failures']} deneme başarısız. Son hata: {error[:300]}",
                Priority.HIGH,
            )

    # ------------------------------------------------------------------ döngüler
    async def _every(self, name: str, interval: float, fn, initial_delay: float = 0) -> None:
        if initial_delay:
            await asyncio.sleep(initial_delay)
        while True:
            started = time.monotonic()
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("'%s' görevi hata verdi", name)
            self.last_run[name] = time.time()
            await asyncio.sleep(max(5.0, interval - (time.monotonic() - started)))

    async def _scanner_loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            due = self._due_games()
            batch = due[: self.cfg.roblox.batch_size]
            for row in batch:
                try:
                    await self.scan_roblox(row["key"])
                    if self.store.source_ok("roblox", time.time()):
                        await self._system_alert("Roblox kaynağı yeniden çalışıyor", "Veri akışı normale döndü.", Priority.LOW)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("Roblox taraması başarısız (%s): %s", row["title"], exc)
                    await self._source_failure("roblox", str(exc))
                    await asyncio.sleep(30)
                    break
            self.last_run["roblox"] = time.time()
            polled_all = all(s in self._charts_polled for s in self._enabled_sources())
            # Roblox uzun süre ulaşılamazsa bildirimler sonsuza dek bastırılmasın
            timed_out = time.time() - self.started_at > BOOTSTRAP_MAX_SECONDS
            if self.bootstrapping and ((polled_all and not self._due_games()) or timed_out):
                await self._finish_bootstrap()
            if len(due) > len(batch):
                await asyncio.sleep(1)
                continue
            self._wake_scanner.clear()
            try:
                await asyncio.wait_for(self._wake_scanner.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def _digest_loop(self) -> None:
        hours = self.cfg.notifications.digest_hours
        if hours <= 0:
            return
        while True:
            last = float(self.store.get_kv("last_digest_ts", "0") or 0)
            wait = last + hours * HOUR - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, HOUR))
                continue
            if not self.bootstrapping:
                try:
                    await self.send_digest()
                except Exception:
                    log.exception("Özet gönderilemedi")
                self.store.set_kv("last_digest_ts", str(time.time()))
            else:
                await asyncio.sleep(300)

    async def prune(self) -> None:
        self.store.prune(time.time() - 30 * DAY)

    def _enabled_sources(self) -> list[str]:
        return [s for s, on in (("steam", self.cfg.steam.enabled), ("epic", self.cfg.epic.enabled)) if on]

    async def run_forever(self) -> None:
        self.bootstrapping = (
            self.cfg.notifications.startup_summary and self.cfg.roblox.enabled and self.store.decisions_count() == 0
        )
        if self.bootstrapping:
            log.info("İlk çalıştırma: başlangıç taraması bitene kadar tekil bildirimler tek özet halinde toplanacak")
        c = self.cfg
        tasks = []
        if c.steam.enabled:
            tasks.append(self._every("steam", c.steam.interval_minutes * 60, self.poll_steam))
            tasks.append(self._every("ccu", c.steam.ccu_interval_minutes * 60, self.poll_ccu, initial_delay=90))
        if c.epic.enabled:
            tasks.append(self._every("epic", c.epic.interval_minutes * 60, self.poll_epic, initial_delay=5))
        if c.roblox.enabled:
            tasks.append(self._scanner_loop())
            tasks.append(self._every(
                "clones", c.roblox.clone_refresh_minutes * 60, self.refresh_clones,
                initial_delay=c.roblox.clone_refresh_minutes * 60,
            ))
        tasks.append(self._digest_loop())
        tasks.append(self._every("prune", DAY, self.prune, initial_delay=HOUR))
        await asyncio.gather(*tasks)

    # ------------------------------------------------------------------ sorgular (web + komutlar)
    async def adhoc_check(self, title: str) -> dict:
        ctx, source = await self.context_for_title(title)
        result = await self.lookup_roblox(ctx)
        a = assess_roblox([(g, ev.weight) for g, ev in result.matches], None, self.cfg.scoring)
        as_dict = lambda g, ev: {  # noqa: E731
            "name": g.name, "creator": g.creator, "playing": g.playing, "visits": g.visits,
            "created": g.created, "url": g.url, "similarity": ev.score, "kind": ev.kind,
            "reason": ev.reason(), "evidence": ev.to_dict(),
        }
        return {
            "title": title, "resolved_title": ctx.title, "context_source": source, "tags": ctx.tags[:10],
            "queries": result.queries, "generic": result.generic,
            "laya": self.laya.available,
            "status": a.status.value, "status_label": a.status.label, "saturation": a.saturation,
            "total_playing": a.total_playing, "clone_count": a.clone_count, "similar_count": a.similar_count,
            "matches": [as_dict(*m) for m in result.matches],
            "related": [as_dict(*m) for m in result.related],
        }

    def game_detail(self, key: str) -> dict | None:
        game = self.store.get_game(key)
        if not game:
            return None
        now = time.time()
        full = self.store.last_roblox_check(key, full_scan_only=True)
        related = json.loads(full["related"]).get("related", []) if full and full["related"] else []
        return {
            "game": game,
            "decision": self.store.get_decision(key),
            "ranks": self.store.active_ranks(key, self.fresh_since(now)),
            "rank_history": self.store.rank_history(key, now - 7 * DAY),
            "ccu": self.store.metric_series(key, "ccu", now - 7 * DAY),
            "roblox": self.store.linked_roblox_games(key),
            "roblox_related": related,
            "roblox_history": self.store.roblox_check_history(key, now - 7 * DAY),
            "alerts": self.store.recent_alerts(20, key=key),
        }

    def status(self) -> dict:
        return {
            "started_at": self.started_at,
            "uptime": time.time() - self.started_at,
            "bootstrapping": self.bootstrapping,
            "last_run": self.last_run,
            "stats": self.store.stats(),
            "sources": self.store.source_health(),
            "channels": self.notifier.channel_health() if self.notifier else [],
            "judges": {
                "laya": {"enabled": self.laya.available, "mode": self.cfg.laya.mode, "model": self.laya.checkpoint,
                         "last_error": self.laya.last_error},
            },
            "queue": len(self._due_games()),
        }

    async def command(self, cmd: str, arg: str) -> str:
        """Telegram komutları. HTML döner."""
        e = html.escape
        if cmd in ("firsatlar", "firsat", "top", "opportunities"):
            rows = self.board([Decision.OPPORTUNITY.value, Decision.RISING_TREND.value, Decision.WATCH.value], limit=15)
            if not rows:
                return "Şu an eşik üstü fırsat yok. Sistem izlemeye devam ediyor."
            lines = ["<b>Güncel fırsatlar</b>", ""]
            for line, r in zip(self._summary_lines(rows), rows):
                lines.append(f'{e(line)} — <a href="{e(r["url"] or "", quote=True)}">mağaza</a>')
            return "\n".join(lines)
        if cmd in ("durum", "status"):
            st = self.status()
            up = int(st["uptime"] // 3600)
            dec = st["stats"]["decisions"]
            lines = [
                "<b>Akım durumu</b>",
                f"Çalışma süresi: {up} saat · Roblox kuyruğu: {st['queue']}",
                f"Takip edilen oyun: {st['stats']['games']} · Roblox benzeri: {st['stats']['roblox_games']}",
                "Kararlar: " + ", ".join(f"{Decision(k).label}: {v}" for k, v in dec.items()),
                "",
            ]
            for s in st["sources"]:
                ok = "✅" if not s["consecutive_failures"] else f"⚠️ {s['consecutive_failures']} hata"
                lines.append(f"{e(s['source'])}: {ok}")
            return "\n".join(lines)
        if cmd in ("kontrol", "check"):
            if not arg:
                return "Kullanım: /kontrol &lt;oyun adı&gt;"
            r = await self.adhoc_check(arg)
            lines = [
                f"<b>{e(r['resolved_title'])}</b> → <b>{e(r['status_label'])}</b> "
                f"({r['clone_count']} klon, {r['similar_count']} benzer · doygunluk {r['saturation']:.2f})",
                f"<i>Bağlam: {e(r['context_source'])}</i>",
            ]
            if r["generic"]:
                lines.append("⚠️ Genel bir isim; eşleşmeleri elle doğrula.")
            if r["matches"]:
                lines += ["", "<b>Roblox'taki benzerleri:</b>"]
                for mt in r["matches"][:8]:
                    tag = "🎯 klon" if mt["kind"] == "clone" else "≈ benzer"
                    lines.append(
                        f'• %{round(mt["similarity"] * 100)} {tag} — <a href="{e(mt["url"], quote=True)}">'
                        f'{e(mt["name"][:60])}</a> ({_fmt_int(mt["playing"])} oyuncu, {_fmt_int(mt["visits"])} ziyaret)'
                        f"\n   <i>{e(mt['reason'])}</i>"
                    )
            else:
                lines.append("Roblox'ta benzer oynanışa sahip oyun bulunamadı.")
            if r["related"]:
                lines += ["", "En yakın diğer adaylar: " + e(", ".join(
                    f"{x['name'][:28]} (%{round(x['similarity'] * 100)})" for x in r["related"][:5]))]
            return "\n".join(lines)
        if cmd in ("oyun", "game"):
            if not arg:
                return "Kullanım: /oyun &lt;oyun adı&gt;"
            row = self.store._one("SELECT key FROM games WHERE title LIKE ? ORDER BY last_seen DESC LIMIT 1", (f"%{arg}%",))
            if not row:
                return "Takip edilen oyunlarda bulunamadı. Anlık kontrol için /kontrol kullan."
            d = self.game_detail(row["key"])
            g, dec = d["game"], d["decision"]
            lines = [f'<b>{e(g["title"])}</b> (<a href="{e(g["url"] or "", quote=True)}">mağaza</a>)']
            lines.append(f"Yayıncı: {e(', '.join(g['publishers']) or '-')} · Bağımsızlık: {g['indie_score']:.2f}")
            if dec:
                lines.append(f"Karar: <b>{Decision(dec['decision']).label}</b> · skor {dec['score']:.2f}")
                lines.append("Gerekçe: " + e("; ".join(dec["reasons"])))
            for r in d["ranks"]:
                lines.append(f"• {e(chart_label(r['chart']))}: #{r['rank']}")
            for c in d["roblox"][:5]:
                tag = "klon" if c.get("kind") == "clone" else "benzer"
                lines.append(
                    f"• Roblox ({tag} %{round((c.get('similarity') or 0) * 100)}): {e(c['name'][:50])} — "
                    f"{_fmt_int(c['playing'])} oyuncu"
                )
            return "\n".join(lines)
        return (
            "<b>Akım komutları</b>\n"
            "/firsatlar — güncel fırsatlar\n"
            "/kontrol &lt;oyun&gt; — bir oyunu anında Roblox'ta kontrol et\n"
            "/oyun &lt;oyun&gt; — takip edilen oyunun detayı\n"
            "/durum — sistem durumu"
        )
