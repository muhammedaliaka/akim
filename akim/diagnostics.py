"""Ham hata metnini sade Türkçe nedene çevirir.

Bildirimlerde, panelde ve raporda istisna metni ya da dosya yolu gösterilmez: kullanıcı "ne oldu, ne yapıyorsun,
ben ne yapmalıyım" sorusunun cevabını tek bakışta alır. Ayrıntı yalnızca log'a yazılır.
"""

from __future__ import annotations

import re

# (aranan ifadeler, neden): ilk eşleşen kazanır; küçük harfe çevrilmiş metinde aranır
_RULES: list[tuple[tuple[str, ...], str]] = [
    (("name or service not known", "temporary failure in name resolution", "nodename nor servname",
      "getaddrinfo", "no address associated"), "adres çözülemedi (internet yok ya da site engelli)"),
    (("network is unreachable", "no route to host", "cannot connect to host"), "internet bağlantısı yok ya da site engelli"),
    (("connection reset", "connection refused", "server disconnected", "broken pipe", "connection aborted"),
     "bağlantı kesildi (ağ ya da engel/VPN kaynaklı olabilir)"),
    (("timed out", "timeout"), "sunucu zamanında yanıt vermedi"),
    (("ssl", "certificate"), "güvenli bağlantı (SSL) kurulamadı"),
    (("http 403", "http 451", "http 401"), "erişim reddedildi (bölge ya da ağ engeli olabilir)"),
    (("http 429",), "çok sık istek yapıldı (hız sınırı)"),
    (("expecting value", "jsondecode", "beklenmeyen yanıt", "unexpected response", "<!doctype", "<html"),
     "beklenmeyen yanıt geldi (engel ya da giriş sayfası olabilir)"),
]
_HTTP_5XX = re.compile(r"http 5\d\d")


def explain_error(text: str) -> str:
    """Ham hata metninden kısa, anlaşılır bir neden üretir."""
    t = (text or "").lower()
    for needles, reason in _RULES:
        if any(n in t for n in needles):
            return reason
    if _HTTP_5XX.search(t):
        return "sunucu tarafında geçici bir hata var"
    return "beklenmeyen bir hata oluştu"


def human_duration(seconds: float) -> str:
    """Saniyeyi '2 sa 5 dk' gibi okunur süreye çevirir."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} gün {hours} sa" if hours else f"{days} gün"
    if hours:
        return f"{hours} sa {minutes} dk" if minutes else f"{hours} sa"
    if minutes:
        return f"{minutes} dk"
    return "1 dk'dan kısa"
