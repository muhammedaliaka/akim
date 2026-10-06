"""SQLite tabanlı kalıcı depolama.

Tek süreç / tek event loop içinde kullanılır; sorgular küçük ve hızlı olduğu için
senkron sqlite3 yeterlidir. WAL modu, web paneli okurken yazmanın sürmesini sağlar.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .analysis.indie import IndieAssessment
from .analysis.matching import title_key
from .models import Alert, ChartEntry, Priority, RobloxGame, StoreGame

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    key TEXT PRIMARY KEY,
    store TEXT NOT NULL,
    store_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT, image TEXT,
    developers TEXT, publishers TEXT, tags TEXT,
    release_ts INTEGER, price_cents INTEGER,
    is_free INTEGER DEFAULT 0, is_early_access INTEGER DEFAULT 0,
    review_count INTEGER, review_pct INTEGER, description TEXT,
    indie_score REAL, indie_tier TEXT, indie_reasons TEXT,
    first_seen REAL, last_seen REAL, title_key TEXT, discount_pct INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chart_state (
    key TEXT NOT NULL, chart TEXT NOT NULL,
    rank INTEGER, prev_rank INTEGER, source_prev_rank INTEGER, metric REAL,
    first_seen REAL, last_seen REAL,
    PRIMARY KEY (key, chart)
);
CREATE TABLE IF NOT EXISTS chart_snapshots (
    key TEXT NOT NULL, chart TEXT NOT NULL, rank INTEGER, metric REAL, ts REAL
);
CREATE INDEX IF NOT EXISTS ix_snap_key ON chart_snapshots(key, ts);
CREATE TABLE IF NOT EXISTS game_metrics (key TEXT NOT NULL, metric TEXT NOT NULL, value REAL, ts REAL);
CREATE INDEX IF NOT EXISTS ix_metric ON game_metrics(key, metric, ts);
CREATE TABLE IF NOT EXISTS roblox_games (
    universe_id INTEGER PRIMARY KEY, root_place_id INTEGER, name TEXT, creator TEXT, description TEXT,
    playing INTEGER, visits INTEGER, up_votes INTEGER, down_votes INTEGER, favorites INTEGER,
    created TEXT, updated TEXT, first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS roblox_metrics (universe_id INTEGER NOT NULL, playing INTEGER, visits INTEGER, ts REAL);
CREATE INDEX IF NOT EXISTS ix_rmetric ON roblox_metrics(universe_id, ts);
CREATE TABLE IF NOT EXISTS roblox_links (
    key TEXT NOT NULL, universe_id INTEGER NOT NULL, similarity REAL, reason TEXT,
    first_seen REAL, last_seen REAL,
    PRIMARY KEY (key, universe_id)
);
CREATE TABLE IF NOT EXISTS roblox_checks (
    key TEXT NOT NULL, ts REAL, status TEXT, saturation REAL, match_count INTEGER,
    total_playing INTEGER, top_visits INTEGER, related TEXT, full_scan INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_rcheck ON roblox_checks(key, ts);
CREATE TABLE IF NOT EXISTS decisions (
    key TEXT PRIMARY KEY, decision TEXT, score REAL, indie REAL, momentum REAL, saturation REAL,
    roblox_status TEXT, reasons TEXT, updated_at REAL, since REAL, last_roblox_scan REAL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, priority INTEGER, title TEXT, body TEXT,
    game_key TEXT, url TEXT, image TEXT, fields TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts);
CREATE TABLE IF NOT EXISTS alert_state (key TEXT NOT NULL, type TEXT NOT NULL, last_ts REAL, PRIMARY KEY (key, type));
CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY, last_ok REAL, last_error TEXT, last_error_ts REAL,
    consecutive_failures INTEGER DEFAULT 0, alerted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS trailers (
    video_id TEXT PRIMARY KEY, channel_id TEXT, channel TEXT, title TEXT, game TEXT, title_key TEXT, kind TEXT,
    published_ts REAL, url TEXT, image TEXT, views INTEGER, first_seen REAL, last_seen REAL
);
CREATE INDEX IF NOT EXISTS ix_trailers_tkey ON trailers(title_key);
CREATE TABLE IF NOT EXISTS trailer_metrics (video_id TEXT NOT NULL, views INTEGER, ts REAL);
CREATE INDEX IF NOT EXISTS ix_trailer_metrics ON trailer_metrics(video_id, ts);
CREATE TABLE IF NOT EXISTS upcoming (
    title_key TEXT PRIMARY KEY, title TEXT, state TEXT, steam_key TEXT, store_url TEXT, release_ts REAL,
    indie REAL, publishers TEXT, tags TEXT, description TEXT, image TEXT,
    best_video TEXT, video_url TEXT, video_ts REAL, channel TEXT, kind TEXT, views INTEGER, vph REAL, buzz REAL,
    roblox_status TEXT, roblox_note TEXT, roblox_ts REAL,
    first_seen REAL, updated_at REAL, resolved_at REAL, alerted_at REAL
);
CREATE INDEX IF NOT EXISTS ix_upcoming_state ON upcoming(state, buzz);
CREATE TABLE IF NOT EXISTS outages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, started_at REAL, ended_at REAL, reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_outages ON outages(source, started_at);
CREATE TABLE IF NOT EXISTS laya_scores (
    title_key TEXT NOT NULL, universe_id INTEGER NOT NULL, fingerprint TEXT, similarity REAL, relation TEXT,
    probabilities TEXT, confidence REAL, embedding REAL, checkpoint TEXT, ts REAL,
    PRIMARY KEY (title_key, universe_id)
);
"""

# Sonradan eklenen sütunlar: (tablo, sütun, tanım). Eski veritabanları açılışta güncellenir.
MIGRATIONS = [
    ("games", "discount_pct", "INTEGER DEFAULT 0"),
    ("roblox_games", "genre", "TEXT"),
    ("roblox_links", "kind", "TEXT DEFAULT 'clone'"),
    ("roblox_links", "weight", "REAL DEFAULT 1.0"),
    ("roblox_links", "evidence", "TEXT"),
    ("roblox_checks", "clone_count", "INTEGER"),
]


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


class Storage:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Eski veritabanlarına sonradan eklenen sütunları ekler."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(games)").fetchall()}
        if "title_key" not in cols:
            self.db.execute("ALTER TABLE games ADD COLUMN title_key TEXT")
            for r in self.db.execute("SELECT key, title FROM games").fetchall():
                self.db.execute("UPDATE games SET title_key=? WHERE key=?", (title_key(r["title"]), r["key"]))
        for table, col, ddl in MIGRATIONS:
            existing = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in existing:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_games_tkey ON games(title_key)")

    def close(self) -> None:
        self.db.close()

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        return _row(self.db.execute(sql, tuple(params)).fetchone())

    # ------------------------------------------------------------------ kv
    def get_kv(self, k: str, default: str | None = None) -> str | None:
        r = self._one("SELECT v FROM kv WHERE k=?", (k,))
        return r["v"] if r else default

    def set_kv(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    # ------------------------------------------------------------------ oyunlar
    def upsert_game(self, g: StoreGame, indie: IndieAssessment, now: float) -> None:
        existing = self._one("SELECT * FROM games WHERE key=?", (g.key,))
        # Detayı eksik bir liste girdisi (ör. yalnızca appid + başlık) mevcut zengin veriyi ezmesin;
        # bu durumda yalnızca "son görülme" güncellenir.
        if existing and not g.publishers and not g.developers and existing["publishers"] not in (None, "[]"):
            self.db.execute("UPDATE games SET last_seen=? WHERE key=?", (now, g.key))
            return
        title = existing["title"] if existing and g.title.startswith("App ") else g.title
        self.db.execute(
            """
            INSERT INTO games(key, store, store_id, title, url, image, developers, publishers, tags,
                release_ts, price_cents, is_free, is_early_access, review_count, review_pct, description,
                indie_score, indie_tier, indie_reasons, first_seen, last_seen, title_key, discount_pct)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                title=excluded.title, url=COALESCE(NULLIF(excluded.url,''), games.url),
                image=COALESCE(NULLIF(excluded.image,''), games.image), developers=excluded.developers,
                publishers=excluded.publishers,
                tags=CASE WHEN excluded.tags='[]' THEN games.tags ELSE excluded.tags END,
                release_ts=COALESCE(excluded.release_ts, games.release_ts), price_cents=excluded.price_cents,
                is_free=excluded.is_free, is_early_access=excluded.is_early_access,
                review_count=COALESCE(excluded.review_count, games.review_count),
                review_pct=COALESCE(excluded.review_pct, games.review_pct),
                description=COALESCE(NULLIF(excluded.description,''), games.description),
                indie_score=excluded.indie_score, indie_tier=excluded.indie_tier, indie_reasons=excluded.indie_reasons,
                last_seen=excluded.last_seen, title_key=excluded.title_key, discount_pct=excluded.discount_pct
            """,
            (
                g.key, g.store, g.store_id, title, g.url, g.image,
                json.dumps(g.developers), json.dumps(g.publishers), json.dumps(g.tags),
                g.release_ts, g.price_cents, int(g.is_free), int(g.is_early_access),
                g.review_count, g.review_pct, g.description,
                indie.score, indie.tier, json.dumps(indie.reasons, ensure_ascii=False),
                now, now, title_key(title), g.discount_pct,
            ),
        )

    def get_game(self, key: str) -> dict | None:
        g = self._one("SELECT * FROM games WHERE key=?", (key,))
        if g:
            for col in ("developers", "publishers", "tags", "indie_reasons"):
                g[col] = json.loads(g[col] or "[]")
        return g

    def tracked_games(self, since: float, min_indie: float) -> list[dict]:
        return self._all(
            "SELECT * FROM games WHERE last_seen>=? AND indie_score>=? ORDER BY last_seen DESC", (since, min_indie)
        )

    # ------------------------------------------------------------------ listeler
    def chart_state(self, chart: str) -> dict[str, dict]:
        return {r["key"]: r for r in self._all("SELECT * FROM chart_state WHERE chart=?", (chart,))}

    def record_rank(self, e: ChartEntry, prev: dict | None, now: float) -> None:
        self.db.execute(
            """
            INSERT INTO chart_state(key, chart, rank, prev_rank, source_prev_rank, metric, first_seen, last_seen)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(key, chart) DO UPDATE SET
                prev_rank=chart_state.rank, rank=excluded.rank, source_prev_rank=excluded.source_prev_rank,
                metric=excluded.metric, last_seen=excluded.last_seen
            """,
            (e.game.key, e.chart, e.rank, None, e.prev_rank, e.metric, prev["first_seen"] if prev else now, now),
        )
        self.db.execute(
            "INSERT INTO chart_snapshots(key, chart, rank, metric, ts) VALUES(?,?,?,?,?)",
            (e.game.key, e.chart, e.rank, e.metric, now),
        )

    def active_ranks(self, key: str, since: float) -> list[dict]:
        return self._all("SELECT * FROM chart_state WHERE key=? AND last_seen>=?", (key, since))

    def rank_history(self, key: str, since: float) -> list[dict]:
        return self._all(
            "SELECT chart, rank, metric, ts FROM chart_snapshots WHERE key=? AND ts>=? ORDER BY ts", (key, since)
        )

    def first_chart_seen(self, key: str) -> float | None:
        r = self._one("SELECT MIN(first_seen) AS t FROM chart_state WHERE key=?", (key,))
        return r["t"] if r else None

    # ------------------------------------------------------------------ metrikler
    def add_metric(self, key: str, metric: str, value: float, now: float) -> None:
        self.db.execute("INSERT INTO game_metrics(key, metric, value, ts) VALUES(?,?,?,?)", (key, metric, value, now))

    def metric_series(self, key: str, metric: str, since: float) -> list[dict]:
        return self._all(
            "SELECT value, ts FROM game_metrics WHERE key=? AND metric=? AND ts>=? ORDER BY ts", (key, metric, since)
        )

    def last_metric(self, key: str, metric: str) -> dict | None:
        return self._one(
            "SELECT value, ts FROM game_metrics WHERE key=? AND metric=? ORDER BY ts DESC LIMIT 1", (key, metric)
        )

    # ------------------------------------------------------------------ roblox
    def upsert_roblox_games(self, games: Iterable[RobloxGame], now: float) -> None:
        for g in games:
            self.db.execute(
                """
                INSERT INTO roblox_games(universe_id, root_place_id, name, creator, description, playing, visits,
                    up_votes, down_votes, favorites, created, updated, first_seen, last_seen, genre)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(universe_id) DO UPDATE SET
                    root_place_id=excluded.root_place_id, name=excluded.name,
                    creator=COALESCE(NULLIF(excluded.creator,''), roblox_games.creator),
                    description=COALESCE(NULLIF(excluded.description,''), roblox_games.description),
                    playing=excluded.playing,
                    visits=MAX(excluded.visits, COALESCE(roblox_games.visits,0)),
                    up_votes=MAX(excluded.up_votes, COALESCE(roblox_games.up_votes,0)),
                    down_votes=MAX(excluded.down_votes, COALESCE(roblox_games.down_votes,0)),
                    favorites=MAX(excluded.favorites, COALESCE(roblox_games.favorites,0)),
                    created=COALESCE(excluded.created, roblox_games.created),
                    updated=COALESCE(excluded.updated, roblox_games.updated),
                    genre=COALESCE(NULLIF(excluded.genre,''), roblox_games.genre),
                    last_seen=excluded.last_seen
                """,
                (
                    g.universe_id, g.root_place_id, g.name, g.creator, g.description, g.playing, g.visits,
                    g.up_votes, g.down_votes, g.favorites, g.created, g.updated, now, now, g.genre,
                ),
            )
            self.db.execute(
                "INSERT INTO roblox_metrics(universe_id, playing, visits, ts) VALUES(?,?,?,?)",
                (g.universe_id, g.playing, g.visits, now),
            )

    def set_links(self, key: str, links: list[dict], now: float) -> list[int]:
        """Oyunun güncel Roblox eşleşmelerini yazar; yeni eklenen universe_id'leri döner.

        links: {universe_id, similarity, reason, kind, weight, evidence} sözlükleri.
        """
        old = {r["universe_id"]: r for r in self._all("SELECT * FROM roblox_links WHERE key=?", (key,))}
        self.db.execute("DELETE FROM roblox_links WHERE key=?", (key,))
        new_ids = []
        for link in links:
            uid = link["universe_id"]
            first = old[uid]["first_seen"] if uid in old else now
            if uid not in old:
                new_ids.append(uid)
            self.db.execute(
                """INSERT INTO roblox_links(key, universe_id, similarity, reason, first_seen, last_seen, kind, weight, evidence)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (key, uid, link["similarity"], link["reason"], first, now, link.get("kind", "clone"),
                 link.get("weight", 1.0), json.dumps(link.get("evidence") or {}, ensure_ascii=False)),
            )
        return new_ids

    def linked_roblox_games(self, key: str) -> list[dict]:
        return self._all(
            """
            SELECT r.*, l.similarity, l.reason, l.kind, l.weight, l.evidence, l.first_seen AS linked_since
            FROM roblox_links l JOIN roblox_games r ON r.universe_id=l.universe_id
            WHERE l.key=? ORDER BY (l.kind='clone') DESC, l.similarity DESC, r.playing DESC
            """,
            (key,),
        )

    def keys_with_links(self, keys: Iterable[str]) -> dict[str, list[tuple[int, float]]]:
        """oyun anahtarı -> [(universe_id, doygunluk ağırlığı)]"""
        keys = list(keys)
        if not keys:
            return {}
        q = ",".join("?" * len(keys))
        out: dict[str, list[tuple[int, float]]] = {}
        for r in self._all(f"SELECT key, universe_id, weight FROM roblox_links WHERE key IN ({q})", keys):
            out.setdefault(r["key"], []).append((r["universe_id"], r["weight"] if r["weight"] is not None else 1.0))
        return out

    def add_roblox_check(
        self, key: str, now: float, status: str, saturation: float, match_count: int,
        total_playing: int, top_visits: int, related: dict | None, full_scan: bool, clone_count: int | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO roblox_checks(key, ts, status, saturation, match_count, total_playing, top_visits, related,
                   full_scan, clone_count)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (key, now, status, saturation, match_count, total_playing, top_visits,
             json.dumps(related, ensure_ascii=False) if related is not None else None, int(full_scan),
             match_count if clone_count is None else clone_count),
        )

    def last_roblox_check(self, key: str, full_scan_only: bool = False) -> dict | None:
        extra = " AND full_scan=1" if full_scan_only else ""
        return self._one(f"SELECT * FROM roblox_checks WHERE key=?{extra} ORDER BY ts DESC LIMIT 1", (key,))

    def roblox_baseline(self, key: str, since: float, before: float) -> int | None:
        r = self._one(
            "SELECT MIN(total_playing) AS m, COUNT(*) AS c FROM roblox_checks WHERE key=? AND ts>=? AND ts<?",
            (key, since, before),
        )
        return int(r["m"]) if r and r["c"] else None

    def roblox_check_history(self, key: str, since: float) -> list[dict]:
        return self._all(
            "SELECT ts, status, saturation, match_count, total_playing FROM roblox_checks WHERE key=? AND ts>=? ORDER BY ts",
            (key, since),
        )

    # ------------------------------------------------------------------ Laya kararları (önbellek)
    def get_laya_score(self, tkey: str, universe_id: int) -> dict | None:
        return self._one("SELECT * FROM laya_scores WHERE title_key=? AND universe_id=?", (tkey, universe_id))

    def save_laya_score(
        self, tkey: str, universe_id: int, fingerprint: str, similarity: float, relation: str, probabilities: str,
        confidence: float, embedding: float | None, checkpoint: str, now: float,
    ) -> None:
        self.db.execute(
            """INSERT INTO laya_scores(title_key, universe_id, fingerprint, similarity, relation, probabilities,
                   confidence, embedding, checkpoint, ts) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(title_key, universe_id) DO UPDATE SET fingerprint=excluded.fingerprint,
                   similarity=excluded.similarity, relation=excluded.relation, probabilities=excluded.probabilities,
                   confidence=excluded.confidence, embedding=excluded.embedding, checkpoint=excluded.checkpoint,
                   ts=excluded.ts""",
            (tkey, universe_id, fingerprint, similarity, relation, probabilities, confidence, embedding, checkpoint, now),
        )

    def enrich_game(self, key: str, tags: list[str], description: str) -> None:
        """Etiketi olmayan (ör. Epic) oyuna başka mağazadan bulunan etiket/açıklamayı ekler."""
        self.db.execute(
            """UPDATE games SET tags=?, description=CASE WHEN length(COALESCE(description,'')) < length(?)
                   THEN ? ELSE description END WHERE key=?""",
            (json.dumps(tags), description, description, key),
        )

    # ------------------------------------------------------------------ kararlar
    def get_decision(self, key: str) -> dict | None:
        d = self._one("SELECT * FROM decisions WHERE key=?", (key,))
        if d:
            d["reasons"] = json.loads(d["reasons"] or "[]")
        return d

    def save_decision(self, key: str, d: dict, now: float) -> None:
        prev = self._one("SELECT decision, since, last_roblox_scan FROM decisions WHERE key=?", (key,))
        since = prev["since"] if prev and prev["decision"] == d["decision"] else now
        last_scan = d.get("last_roblox_scan") or (prev["last_roblox_scan"] if prev else None)
        self.db.execute(
            """
            INSERT INTO decisions(key, decision, score, indie, momentum, saturation, roblox_status, reasons,
                updated_at, since, last_roblox_scan)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET decision=excluded.decision, score=excluded.score, indie=excluded.indie,
                momentum=excluded.momentum, saturation=excluded.saturation, roblox_status=excluded.roblox_status,
                reasons=excluded.reasons, updated_at=excluded.updated_at, since=excluded.since,
                last_roblox_scan=excluded.last_roblox_scan
            """,
            (
                key, d["decision"], d["score"], d["indie"], d["momentum"], d["saturation"], d["roblox_status"],
                json.dumps(d.get("reasons") or [], ensure_ascii=False), now, since, last_scan,
            ),
        )

    def decisions_count(self) -> int:
        return self._one("SELECT COUNT(*) AS c FROM decisions")["c"]

    def board(
        self, decisions: Iterable[str] | None = None, limit: int = 100, since: float | None = None,
        fresh_since: float = 0,
    ) -> list[dict]:
        """Panel ve özetler için karar + oyun bilgisi birleşik liste.

        fresh_since: bu zamandan sonra görülen liste sıraları "güncel" sayılır.
        """
        where, params = [], [fresh_since, fresh_since]
        if decisions:
            decisions = list(decisions)
            where.append(f"d.decision IN ({','.join('?' * len(decisions))})")
            params += decisions
        if since:
            where.append("g.last_seen>=?")
            params.append(since)
        sql = f"""
            SELECT d.*, g.title, g.title_key, g.store, g.url, g.image, g.publishers, g.developers, g.tags,
                   g.release_ts, g.indie_tier, g.last_seen, g.first_seen,
                   (SELECT MIN(rank) FROM chart_state c WHERE c.key=d.key AND c.last_seen>=?) AS best_rank,
                   (SELECT GROUP_CONCAT(chart || ':' || rank) FROM chart_state c WHERE c.key=d.key AND c.last_seen>=?) AS charts
            FROM decisions d JOIN games g ON g.key=d.key
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY CASE d.decision WHEN 'opportunity' THEN 0 WHEN 'rising_trend' THEN 1 WHEN 'watch' THEN 2
                     WHEN 'low' THEN 3 WHEN 'saturated' THEN 4 ELSE 5 END, d.score DESC
            LIMIT ?
        """
        rows = self._all(sql, params + [limit])
        for r in rows:
            r["reasons"] = json.loads(r["reasons"] or "[]")
            for col in ("publishers", "developers", "tags"):
                r[col] = json.loads(r[col] or "[]")
        return rows

    def games_due_for_roblox(self, now: float, since: float, min_indie: float, normal_age: float, hot_age: float) -> list[dict]:
        return self._all(
            """
            SELECT g.key, g.title, d.decision, d.last_roblox_scan,
                   (SELECT MIN(rank) FROM chart_state c WHERE c.key=g.key AND c.last_seen>=?) AS best_rank
            FROM games g LEFT JOIN decisions d ON d.key=g.key
            WHERE g.last_seen>=? AND g.indie_score>=?
              AND (d.last_roblox_scan IS NULL
                   OR (d.decision IN ('opportunity','rising_trend') AND d.last_roblox_scan < ?)
                   OR d.last_roblox_scan < ?)
            ORDER BY (d.last_roblox_scan IS NOT NULL), best_rank IS NULL, best_rank
            """,
            (since, since, min_indie, now - hot_age, now - normal_age),
        )

    # ------------------------------------------------------------------ bildirimler
    def add_alert(self, a: Alert) -> int:
        cur = self.db.execute(
            "INSERT INTO alerts(type, priority, title, body, game_key, url, image, fields, ts) VALUES(?,?,?,?,?,?,?,?,?)",
            (a.type, int(a.priority), a.title, a.body, a.game_key, a.url, a.image,
             json.dumps(a.fields, ensure_ascii=False), a.ts),
        )
        return int(cur.lastrowid)

    def recent_alerts(self, limit: int = 50, key: str | None = None) -> list[dict]:
        if key:
            rows = self._all("SELECT * FROM alerts WHERE game_key=? ORDER BY ts DESC LIMIT ?", (key, limit))
        else:
            rows = self._all("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["fields"] = json.loads(r["fields"] or "[]")
            r["priority"] = Priority(r["priority"]).name.lower()
        return rows

    def alert_last_sent(self, key: str, type_: str) -> float | None:
        r = self._one("SELECT last_ts FROM alert_state WHERE key=? AND type=?", (key, type_))
        return r["last_ts"] if r else None

    def mark_alert_sent(self, key: str, type_: str, now: float) -> None:
        self.db.execute(
            "INSERT INTO alert_state(key, type, last_ts) VALUES(?,?,?) ON CONFLICT(key, type) DO UPDATE SET last_ts=excluded.last_ts",
            (key, type_, now),
        )

    # ------------------------------------------------------------------ sağlık
    def source_ok(self, source: str, now: float) -> bool:
        """Başarılı çağrıyı kaydeder. Daha önce arıza bildirimi yapıldıysa True (toparlandı) döner."""
        prev = self._one("SELECT alerted FROM source_health WHERE source=?", (source,))
        self.db.execute(
            """INSERT INTO source_health(source, last_ok, consecutive_failures, alerted) VALUES(?,?,0,0)
               ON CONFLICT(source) DO UPDATE SET last_ok=excluded.last_ok, consecutive_failures=0, alerted=0""",
            (source, now),
        )
        return bool(prev and prev["alerted"])

    def source_failed(self, source: str, error: str, now: float) -> dict:
        self.db.execute(
            """INSERT INTO source_health(source, last_error, last_error_ts, consecutive_failures) VALUES(?,?,?,1)
               ON CONFLICT(source) DO UPDATE SET last_error=excluded.last_error, last_error_ts=excluded.last_error_ts,
               consecutive_failures=source_health.consecutive_failures+1""",
            (source, error[:500], now),
        )
        return self._one("SELECT * FROM source_health WHERE source=?", (source,))

    def mark_source_alerted(self, source: str) -> None:
        self.db.execute("UPDATE source_health SET alerted=1 WHERE source=?", (source,))

    def source_health(self) -> list[dict]:
        return self._all("SELECT * FROM source_health ORDER BY source")

    # ------------------------------------------------------------------ fragmanlar / yaklaşan oyunlar
    def upsert_trailer(self, v: dict, now: float) -> None:
        self.db.execute(
            """INSERT INTO trailers(video_id, channel_id, channel, title, game, title_key, kind, published_ts, url, image,
                   views, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET views=excluded.views, last_seen=excluded.last_seen,
                   title=excluded.title""",
            (v["video_id"], v["channel_id"], v["channel"], v["title"], v["game"], v["title_key"], v["kind"],
             v["published_ts"], v["url"], v["image"], v["views"], now, now),
        )

    def add_trailer_metric(self, video_id: str, views: int, now: float) -> None:
        self.db.execute("INSERT INTO trailer_metrics(video_id, views, ts) VALUES(?,?,?)", (video_id, views, now))

    def trailer_metric_before(self, video_id: str, before_ts: float) -> dict | None:
        """Verilen zamandan önceki en yeni ölçüm (hız hesabı için)."""
        return self._one(
            "SELECT views, ts FROM trailer_metrics WHERE video_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (video_id, before_ts),
        )

    def get_upcoming(self, tkey: str) -> dict | None:
        row = self._one("SELECT * FROM upcoming WHERE title_key=?", (tkey,))
        return self._decode_upcoming(row)

    @staticmethod
    def _decode_upcoming(row: dict | None) -> dict | None:
        if row:
            for col in ("publishers", "tags"):
                row[col] = json.loads(row[col] or "[]")
        return row

    def save_upcoming(self, u: dict) -> None:
        cols = (
            "title_key", "title", "state", "steam_key", "store_url", "release_ts", "indie", "publishers", "tags",
            "description", "image", "best_video", "video_url", "video_ts", "channel", "kind", "views", "vph", "buzz",
            "roblox_status", "roblox_note", "roblox_ts", "first_seen", "updated_at", "resolved_at", "alerted_at",
        )
        row = {c: u.get(c) for c in cols}
        row["publishers"] = json.dumps(u.get("publishers") or [], ensure_ascii=False)
        row["tags"] = json.dumps(u.get("tags") or [], ensure_ascii=False)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("title_key", "first_seen"))
        self.db.execute(
            f"INSERT INTO upcoming({', '.join(cols)}) VALUES({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(title_key) DO UPDATE SET {updates}",
            tuple(row[c] for c in cols),
        )

    def mark_upcoming_alerted(self, tkey: str, now: float) -> None:
        self.db.execute("UPDATE upcoming SET alerted_at=? WHERE title_key=?", (now, tkey))

    def list_upcoming(self, states: Iterable[str] = ("upcoming", "unlisted"), limit: int = 50, min_buzz: float = 0.0) -> list[dict]:
        states = list(states)
        q = ",".join("?" * len(states))
        rows = self._all(
            f"SELECT * FROM upcoming WHERE state IN ({q}) AND COALESCE(buzz,0)>=? ORDER BY buzz DESC, views DESC LIMIT ?",
            states + [min_buzz, limit],
        )
        return [self._decode_upcoming(r) for r in rows]

    # ------------------------------------------------------------------ kesintiler (rapor için)
    def open_outage(self, source: str, now: float, reason: str) -> None:
        if self._one("SELECT id FROM outages WHERE source=? AND ended_at IS NULL", (source,)):
            return
        self.db.execute(
            "INSERT INTO outages(source, started_at, reason) VALUES(?,?,?)", (source, now, reason[:200])
        )

    def close_outage(self, source: str, now: float) -> float | None:
        """Açık kesintiyi kapatır; süresini (sn) döner. Açık kesinti yoksa None."""
        row = self._one("SELECT id, started_at FROM outages WHERE source=? AND ended_at IS NULL", (source,))
        if not row:
            return None
        self.db.execute("UPDATE outages SET ended_at=? WHERE id=?", (now, row["id"]))
        return now - row["started_at"]

    def outages_since(self, since: float) -> list[dict]:
        return self._all(
            "SELECT * FROM outages WHERE COALESCE(ended_at, started_at)>=? ORDER BY started_at", (since,)
        )

    # ------------------------------------------------------------------ bakım
    def stats(self) -> dict:
        one = lambda sql, p=(): self._one(sql, p)["c"]  # noqa: E731
        return {
            "games": one("SELECT COUNT(*) AS c FROM games"),
            "roblox_games": one("SELECT COUNT(DISTINCT universe_id) AS c FROM roblox_links"),
            "roblox_clones": one("SELECT COUNT(DISTINCT universe_id) AS c FROM roblox_links WHERE kind='clone'"),
            "alerts": one("SELECT COUNT(*) AS c FROM alerts"),
            "upcoming": one("SELECT COUNT(*) AS c FROM upcoming WHERE state IN ('upcoming','unlisted')"),
            "decisions": {r["decision"]: r["c"] for r in self._all("SELECT decision, COUNT(*) AS c FROM decisions GROUP BY decision")},
        }

    def prune(self, older_than: float) -> None:
        for table in ("chart_snapshots", "game_metrics", "roblox_metrics", "roblox_checks"):
            self.db.execute(f"DELETE FROM {table} WHERE ts<?", (older_than,))
        self.db.execute("DELETE FROM alerts WHERE ts<?", (older_than,))
        self.db.execute("DELETE FROM trailer_metrics WHERE ts<?", (older_than,))
        self.db.execute("DELETE FROM trailers WHERE last_seen<?", (older_than,))
        self.db.execute("DELETE FROM upcoming WHERE updated_at<? AND state IN ('released','filtered')", (older_than,))
        self.db.execute("DELETE FROM outages WHERE COALESCE(ended_at, started_at)<?", (older_than - 60 * 86400,))

