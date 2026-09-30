"""YAML + ortam değişkeni tabanlı yapılandırma.

Tüm ayarların varsayılanı vardır; config dosyası olmadan da sistem çalışır.
YAML içindeki ``${DEGISKEN}`` veya ``${DEGISKEN:-varsayilan}`` ifadeleri ortam
değişkenleriyle doldurulur, böylece gizli bilgiler dosyaya yazılmaz.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .runtime import load_dotenv

log = logging.getLogger(__name__)

# Eski config dosyalarında kalmış olabilecek, artık desteklenmeyen bölümler: hata vermek yerine
# uyarıp yok sayılır (eski config.example.yaml'dan kopyalanan config.yaml'lar bozulmasın).
REMOVED_SECTIONS = {
    "llm": "Claude doğrulaması (ücretli API) kaldırıldı; Akım yalnızca ücretsiz kaynaklarla çalışır",
}


@dataclass
class GeneralConfig:
    data_dir: str = "./data"
    country: str = "US"
    language: str = "english"
    log_level: str = "INFO"
    # Bir oyun listelerden düştükten sonra kaç gün daha takip edilsin
    track_days: int = 7
    # Dosyaya da log yaz (boş = yalnızca konsol). Konsolsuz çalışmada (pythonw, Görev Zamanlayıcı) varsayılan:
    # <data_dir>/akim.log. Göreli yollar config dosyasının klasörüne göre çözülür.
    log_file: str = ""
    # Windows'ta bilgisayar boşta uykuya geçmesin (7/24 izleme için). Diğer sistemlerde etkisizdir.
    keep_awake: bool = True


@dataclass
class SteamConfig:
    enabled: bool = True
    # topsellers: anlık en çok satanlar, weekly_topsellers: haftalık, most_played: anlık oyuncu
    charts: list[str] = field(default_factory=lambda: ["topsellers", "weekly_topsellers", "most_played"])
    top_n: int = 100
    interval_minutes: float = 15
    # Takip edilen bağımsız oyunların anlık oyuncu sayısını ölçme sıklığı
    ccu_interval_minutes: float = 30


@dataclass
class EpicConfig:
    enabled: bool = True
    collections: list[str] = field(
        default_factory=lambda: [
            "top-sellers",
            "most-played",
            "trending",
            "most-popular",
            "top-new-releases",
            "top-wishlisted",
            "top-player-reviewed",
        ]
    )
    include_free_games: bool = False
    interval_minutes: float = 30


@dataclass
class RobloxConfig:
    enabled: bool = True
    # Normal takipteki oyunlar için Roblox taraması sıklığı
    recheck_hours: float = 6
    # FIRSAT / AKIM oyunları daha sık taranır
    hot_recheck_hours: float = 1.5
    # Bilinen Roblox klonlarının oyuncu sayısını yenileme sıklığı
    clone_refresh_minutes: float = 15
    max_queries_per_game: int = 3
    max_matches_per_game: int = 15
    request_delay_seconds: float = 1.0
    # Tek analiz döngüsünde en fazla kaç oyun taransın (API'yi yormamak için)
    batch_size: int = 10


@dataclass
class FilterConfig:
    min_indie_score: float = 0.5
    extra_major_publishers: list[str] = field(default_factory=list)
    extra_midtier_publishers: list[str] = field(default_factory=list)
    # Büyük listede olsa bile bağımsız sayılacak yayıncılar
    allow_publishers: list[str] = field(default_factory=list)
    # Bu regex'lerle eşleşen başlıklar tamamen yok sayılır
    ignore_titles: list[str] = field(
        default_factory=lambda: [
            r"(?i)soundtrack", r"(?i)\bdlc\b", r"(?i)\bcrosshair\b", r"(?i)wallpaper engine",
            r"(?i)\bbenchmark\b", r"(?i)\bsdk\b", r"(?i)\bplaytest\b",
        ]
    )


@dataclass
class ScoringConfig:
    # Roblox eşleştirme: isim / açıklama-atfı bu eşiği geçerse "klon"
    clone_threshold: float = 0.72
    # Oynanış (konsept) benzerliği bu eşiği geçerse isim tutmasa da "klon"
    concept_clone_threshold: float = 0.75
    # Bu eşiğin üstü "benzer oynanış": doygunluğa kısmi ağırlıkla katılır
    similar_threshold: float = 0.6
    similar_weight: float = 0.35
    opportunity_threshold: float = 0.65
    watch_threshold: float = 0.5
    # FIRSAT için gereken en düşük momentum (gerçek bir yükseliş olmadan fırsat sayma)
    min_opportunity_momentum: float = 0.3
    min_watch_momentum: float = 0.2
    # Roblox'ta toplam bu kadar anlık oyuncu = tam doygunluk
    saturation_ccu: int = 5000
    saturation_visits: int = 50_000_000
    saturation_match_count: int = 8
    # Klon toplam oyuncu sayısı bu oranda artarsa "yükselişte" sayılır
    rising_ratio: float = 1.8
    rising_min_playing: int = 150
    rank_surge_positions: int = 15
    ccu_surge_ratio: float = 1.5
    ccu_surge_min_players: int = 1000


@dataclass
class LayaConfig:
    """İsteğe bağlı Laya karar modeli (yerel, ücretsiz; PyTorch gerekir). Bkz. eval/README.md."""

    enabled: bool = False
    # evidence: Laya diğer kanıtlara düşük güvenle eklenir | judge: son söz Laya'da (ince ayarlı model için)
    mode: str = "evidence"
    checkpoint: str = ""  # "" = İngilizce kök, "typed-decisions", "multilingual"
    model_path: str = ""  # kendi ince ayarlı checkpoint'in (yerel klasör veya HF deposu)
    device: str = ""  # "" = otomatik, "cpu", "cuda", "mps"
    reliability: float = 0.35  # evidence modunda Laya kanıtının ağırlığı
    max_candidates: int = 8
    min_prescore: float = 0.2
    batch_size: int = 8
    use_embeddings: bool = False


@dataclass
class ChannelConfig:
    enabled: bool = False
    min_priority: str = "medium"
    # Kanal türüne göre anlamlı olan alanlar (hepsi opsiyonel)
    bot_token: str = ""
    chat_id: str = ""
    commands: bool = True  # Telegram: /firsatlar, /kontrol gibi komutları dinle
    webhook_url: str = ""
    server: str = "https://ntfy.sh"
    topic: str = ""
    token: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    starttls: bool = True


def _default_channels() -> dict[str, ChannelConfig]:
    return {"console": ChannelConfig(enabled=True, min_priority="low")}


@dataclass
class NotificationConfig:
    # Aynı oyun + aynı olay türü için tekrar bildirim bekleme süresi
    cooldown_hours: float = 12
    # Periyodik özet (0 = kapalı)
    digest_hours: float = 6
    digest_size: int = 10
    # Sistem ilk açıldığında mevcut her fırsat için bildirim yağdırmamak adına
    # ilk tam taramayı tek bir özet mesajında topla
    startup_summary: bool = True
    channels: dict[str, ChannelConfig] = field(default_factory=_default_channels)


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    # Boş değilse POST uç noktaları "Authorization: Bearer <token>" ister
    token: str = ""


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    steam: SteamConfig = field(default_factory=SteamConfig)
    epic: EpicConfig = field(default_factory=EpicConfig)
    roblox: RobloxConfig = field(default_factory=RobloxConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    laya: LayaConfig = field(default_factory=LayaConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    web: WebConfig = field(default_factory=WebConfig)

    @property
    def db_path(self) -> Path:
        return Path(self.general.data_dir) / "akim.db"


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        # kabuktaki ${X:-varsayılan} gibi: değişken tanımsız VEYA boşsa varsayılan kullanılır
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1)) or m.group(2) or "", value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _coerce(tp: Any, value: Any, path: str) -> Any:
    origin = typing.get_origin(tp)
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise ValueError(f"{path}: sözlük bekleniyordu")
        return _build(tp, value, path)
    if origin is list:
        (item_tp,) = typing.get_args(tp)
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        return [_coerce(item_tp, v, f"{path}[]") for v in (value or [])]
    if origin is dict:
        _, val_tp = typing.get_args(tp)
        return {str(k): _coerce(val_tp, v, f"{path}.{k}") for k, v in (value or {}).items()}
    if tp is bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "evet"}
        return bool(value)
    if tp in (int, float):
        if value in ("", None):
            raise ValueError(f"{path}: sayı bekleniyordu")
        return tp(value)
    if tp is str:
        return "" if value is None else str(value)
    return value


def _build(cls: type, data: dict, path: str = "") -> Any:
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Bilinmeyen ayar(lar) {path or 'kök'}: {', '.join(sorted(unknown))}")
    kwargs = {}
    for name, value in data.items():
        kwargs[name] = _coerce(hints[name], value, f"{path}.{name}" if path else name)
    return cls(**kwargs)


def _resolve_path(value: str, base: Path) -> str:
    """``~`` açılır; göreli yollar ``base`` (config dosyasının klasörü) altında çözülür.

    Görev Zamanlayıcı/systemd çalışma klasörünü (System32, /) kendi seçer; veri o klasöre yazılmamalıdır.
    """
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else base / path)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Config dosyasını yükler. Yol verilmezse AKIM_CONFIG veya ./config.yaml denenir.

    Config dosyasının (yoksa çalışma klasörünün) yanındaki ``.env`` dosyası da okunur; bu sayede Windows ve
    Termux'ta Docker/systemd'nin yaptığı gibi ortam değişkeni tanımlamaya gerek kalmaz.
    """
    candidate = path or os.environ.get("AKIM_CONFIG") or "config.yaml"
    p = Path(candidate)
    base = p.resolve().parent if p.is_file() else Path.cwd()
    load_dotenv([base / ".env", Path.cwd() / ".env"])
    raw: dict = {}
    if p.is_file():
        raw = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    elif path or p.exists():
        # ör. docker, eksik config.yaml için boş bir klasör oluşturmuşsa
        raise FileNotFoundError(f"Config dosyası bulunamadı veya dosya değil: {p}")
    raw = _expand_env(raw)
    for name, why in REMOVED_SECTIONS.items():
        if isinstance(raw, dict) and name in raw:
            raw.pop(name)
            log.warning("config: '%s:' bölümü yok sayıldı (%s); dosyadan silebilirsin", name, why)

    channels = (raw.get("notifications") or {}).get("channels")
    if channels is not None:
        # Kullanıcı kanalları tanımladıysa konsolu varsayılan olarak açık tut
        channels.setdefault("console", {"enabled": True, "min_priority": "low"})

    cfg = _build(Config, raw)
    _apply_env_shortcuts(cfg)
    cfg.general.data_dir = _resolve_path(cfg.general.data_dir, base)
    if cfg.general.log_file:
        cfg.general.log_file = _resolve_path(cfg.general.log_file, base)
    return cfg


def _apply_env_shortcuts(cfg: Config) -> None:
    """Config dosyası olmadan yalnızca ortam değişkenleriyle kanal açabilmek için kısayollar."""
    ch = cfg.notifications.channels
    env = os.environ
    if env.get("AKIM_TELEGRAM_BOT_TOKEN") and env.get("AKIM_TELEGRAM_CHAT_ID") and "telegram" not in ch:
        ch["telegram"] = ChannelConfig(
            enabled=True, bot_token=env["AKIM_TELEGRAM_BOT_TOKEN"], chat_id=env["AKIM_TELEGRAM_CHAT_ID"]
        )
    if env.get("AKIM_DISCORD_WEBHOOK_URL") and "discord" not in ch:
        ch["discord"] = ChannelConfig(enabled=True, webhook_url=env["AKIM_DISCORD_WEBHOOK_URL"])
    if env.get("AKIM_NTFY_TOPIC") and "ntfy" not in ch:
        ch["ntfy"] = ChannelConfig(
            enabled=True,
            topic=env["AKIM_NTFY_TOPIC"],
            server=env.get("AKIM_NTFY_SERVER", "https://ntfy.sh"),
            min_priority="high",
        )
    if env.get("AKIM_SLACK_WEBHOOK_URL") and "slack" not in ch:
        ch["slack"] = ChannelConfig(enabled=True, webhook_url=env["AKIM_SLACK_WEBHOOK_URL"])
    if env.get("AKIM_WEB_TOKEN"):
        cfg.web.token = env["AKIM_WEB_TOKEN"]
    if env.get("AKIM_LAYA_ENABLED"):
        cfg.laya.enabled = env["AKIM_LAYA_ENABLED"].strip().lower() in {"1", "true", "yes", "on", "evet"}
    if env.get("AKIM_DATA_DIR"):
        cfg.general.data_dir = env["AKIM_DATA_DIR"]
    if env.get("AKIM_LOG_FILE"):
        cfg.general.log_file = env["AKIM_LOG_FILE"]
