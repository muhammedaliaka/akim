"""Sistem genelinde kullanılan veri modelleri."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum


@dataclass
class StoreGame:
    """Steam veya Epic mağazasındaki bir oyun."""

    key: str  # "steam:3164500" veya "epic:<namespace>"
    store: str  # "steam" | "epic"
    store_id: str
    title: str
    url: str = ""
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    release_ts: int | None = None
    price_cents: int | None = None
    is_free: bool = False
    is_early_access: bool = False
    image: str = ""
    review_count: int | None = None
    review_pct: int | None = None
    description: str = ""
    kind: str = "game"  # "game" | "software" (Steam'de uygulama türü)
    discount_pct: int = 0  # anlık indirim; indirim kaynaklı sıra tırmanışını ayırt etmek için


@dataclass
class ChartEntry:
    """Bir listedeki (top sellers, most played...) tek satır."""

    chart: str  # ör. "steam_topsellers", "epic_top-sellers"
    rank: int
    game: StoreGame
    prev_rank: int | None = None  # kaynağın verdiği önceki sıra (ör. geçen hafta)
    metric: float | None = None  # ör. anlık oyuncu sayısı
    meta: dict = field(default_factory=dict)


@dataclass
class RobloxGame:
    universe_id: int
    root_place_id: int
    name: str
    description: str = ""
    creator: str = ""
    playing: int = 0
    visits: int = 0
    up_votes: int = 0
    down_votes: int = 0
    favorites: int = 0
    created: str | None = None  # ISO-8601
    updated: str | None = None

    @property
    def url(self) -> str:
        return f"https://www.roblox.com/games/{self.root_place_id}"


@dataclass
class RobloxMatch:
    game: RobloxGame
    similarity: float
    reason: str


class RobloxStatus(str, Enum):
    NONE = "none"  # Roblox'ta karşılığı yok
    EARLY = "early"  # birkaç küçük/zayıf klon var
    RISING = "rising"  # klonlar hızla büyüyor, akım Roblox'a geçiyor
    COMPETITIVE = "competitive"  # oturmuş rakipler var
    SATURATED = "saturated"  # doymuş pazar

    @property
    def label(self) -> str:
        return ROBLOX_STATUS_LABELS[self]


ROBLOX_STATUS_LABELS = {
    RobloxStatus.NONE: "Roblox'ta YOK",
    RobloxStatus.EARLY: "Erken aşama",
    RobloxStatus.RISING: "Roblox'ta yükselişte",
    RobloxStatus.COMPETITIVE: "Rekabetçi",
    RobloxStatus.SATURATED: "Doymuş",
}


class Decision(str, Enum):
    OPPORTUNITY = "opportunity"  # FIRSAT: hemen harekete geç
    RISING_TREND = "rising_trend"  # akım Roblox'a geçti, hızlı ol
    WATCH = "watch"  # takipte tut
    SATURATED = "saturated"  # geç kalındı
    LOW = "low"  # zayıf sinyal
    FILTERED = "filtered"  # büyük yayıncı / kapsam dışı

    @property
    def label(self) -> str:
        return DECISION_LABELS[self]


DECISION_LABELS = {
    Decision.OPPORTUNITY: "FIRSAT",
    Decision.RISING_TREND: "AKIM BAŞLADI",
    Decision.WATCH: "İZLE",
    Decision.SATURATED: "DOYMUŞ",
    Decision.LOW: "ZAYIF",
    Decision.FILTERED: "ELENDİ",
}


class Priority(IntEnum):
    LOW = 10
    MEDIUM = 20
    HIGH = 30
    CRITICAL = 40

    @classmethod
    def parse(cls, value: str | int | Priority) -> Priority:
        if isinstance(value, Priority):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[str(value).strip().upper()]


@dataclass
class Alert:
    """Kanallara gönderilecek bildirim."""

    type: str
    priority: Priority
    title: str
    body: str
    game_key: str | None = None
    url: str | None = None
    image: str | None = None
    fields: list[tuple[str, str]] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    id: int | None = None
    # Tekrar koruması anahtarı; aynı oyun Steam ve Epic'te varsa tek bildirim için
    dedupe_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "priority": self.priority.name.lower(),
            "title": self.title,
            "body": self.body,
            "game_key": self.game_key,
            "url": self.url,
            "image": self.image,
            "fields": [list(f) for f in self.fields],
            "ts": self.ts,
        }
