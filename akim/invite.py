"""Arkadaşları ntfy bildirimlerine katmak için kısa, WhatsApp'a yapıştırılabilir davet metni."""

from __future__ import annotations

import re
import secrets
from urllib.parse import urlsplit

from .config import Config

DEFAULT_SERVER = "https://ntfy.sh"


def ntfy_target(cfg: Config) -> tuple[str, str] | None:
    """Yapılandırmadaki ilk etkin ntfy kanalının (sunucu, konu) çifti; yoksa None."""
    for name, ch in cfg.notifications.channels.items():
        if name.split(":")[0] == "ntfy" and ch.enabled and ch.topic:
            return (ch.server or DEFAULT_SERVER).rstrip("/"), ch.topic
    return None


def build_invite(server: str, topic: str) -> str:
    host = urlsplit(server).netloc or server
    custom_server = server.rstrip("/") != DEFAULT_SERVER
    server_line = (
        f"   Sunucu: {server} (ayarlarda 'Sunucu' kutusuna bunu yaz)" if custom_server else "   Sunucu: ntfy.sh (olduğu gibi bırak)"
    )
    return "\n".join([
        "📲 Akım bildirimlerine katıl (1 dakika)",
        "",
        "1) \"ntfy\" uygulamasını kur:",
        "   • Android: Play Store veya F-Droid",
        "   • iPhone: App Store",
        "2) Uygulamayı aç → ➕ ile yeni konu ekle ve konu adına şunu yaz:",
        f"   {topic}",
        server_line,
        "   → Abone ol",
        "3) Bildirim iznini ver.",
        "   • Xiaomi/HyperOS: Ayarlar → Pil → ntfy → \"Kısıtlama yok\"; ntfy'yi son uygulamalarda kilitle.",
        "",
        "Hepsi bu: fırsatlar ve yaklaşan oyunlar telefonuna anında düşer.",
        f"Kısayol (ntfy kuruluysa açar ve abone eder): ntfy://{host}/{topic}",
        "Konu adı şifre gibidir; başkalarıyla paylaşma.",
    ])


OWNER_NOTE = (
    "Not (yalnızca sana): konu adını bilen herkes mesajları okuyabilir ve konuya mesaj da gönderebilir. Bir arkadaşın "
    "çıkarsa ya da konu adı sızarsa .env içindeki AKIM_NTFY_TOPIC'i değiştir, Akım'ı yeniden başlat ve yeni adı "
    "kalan arkadaşlara gönder. ntfy.sh ücretsiz ve anonimdir; hesap gerekmez."
)


_COMMON = {"akim", "test", "oyun", "oyunlar", "games", "game", "roblox", "steam", "ntfy", "bildirim", "firsat", "alerts"}


def topic_weakness(topic: str) -> str | None:
    """ntfy konu adı parola gibidir (bilen herkes okuyabilir ve yazabilir). Tahmin edilebilir görünüyorsa nedenini döner."""
    t = topic.strip().lower()
    if len(t) < 12:
        return f"çok kısa ({len(t)} karakter); en az 12 karakter olmalı"
    if t in _COMMON or re.fullmatch(r"[a-z]+", t):
        return "tahmin edilebilir bir sözcük; rastgele karakterler içermeli"
    if len(set(t)) < 6:
        return "az çeşitli karakter içeriyor"
    return None


def suggest_topic() -> str:
    return "akim-" + secrets.token_hex(6)
