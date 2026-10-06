"""Son N günün sade özeti: ne bildirildi, hangi kaynak ne kadar kesildi, dikkat edilecekler.

`akim report` ve Telegram `/rapor` kullanır. Yalnızca veritabanını okur (ağ gerekmez); böylece sistemin ne yaptığı
tek metinde görülüp paylaşılabilir.
"""

from __future__ import annotations

import time
from collections import Counter

from .config import Config
from .diagnostics import human_duration
from .storage import Storage

TYPE_LABELS = {
    "opportunity": "fırsat", "rising_trend": "akım başladı", "first_clone": "ilk Roblox klonu",
    "new_clone": "yeni Roblox benzeri", "saturated": "fırsat kapandı", "rank_surge": "sıra sıçraması",
    "ccu_surge": "oyuncu sıçraması", "chart_entry": "listeye giriş", "rising_unverified": "yükseliyor (Roblox'suz)",
    "upcoming_trailer": "yaklaşan oyun (fragman)", "digest": "özet", "bootstrap": "başlangıç özeti",
    "system": "sistem", "test": "test",
}
SOURCE_LABELS = {"steam": "Steam", "epic": "Epic", "roblox": "Roblox", "youtube": "YouTube"}
PRIORITY_LABELS = {10: "düşük", 20: "orta", 30: "yüksek", 40: "kritik"}
HOUR = 3600
DAY = 86400


def _when(ts: float | None) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts)) if ts else "-"


def build_report(store: Storage, cfg: Config, days: float = 7, now: float | None = None) -> str:
    now = now or time.time()
    since = now - days * DAY
    out: list[str] = [f"📊 Akım raporu — son {days:g} gün ({_when(now)})", ""]

    # ---- bildirimler
    alerts = store._all("SELECT type, priority, title, game_key, ts FROM alerts WHERE ts>=? ORDER BY ts", (since,))
    real = [a for a in alerts if a["type"] != "test"]
    by_type = Counter(a["type"] for a in real)
    by_prio = Counter(a["priority"] for a in real)
    out.append(f"BİLDİRİMLER: {len(real)} adet")
    if real:
        out.append("  " + " · ".join(f"{TYPE_LABELS.get(t, t)} {n}" for t, n in by_type.most_common()))
        out.append("  öncelik: " + " · ".join(
            f"{PRIORITY_LABELS[p]} {by_prio[p]}" for p in (40, 30, 20, 10) if by_prio.get(p)
        ))
    out.append("")

    # ---- kaynak sağlığı ve kesintiler
    out.append("KAYNAKLAR")
    outages = store.outages_since(since)
    health = {h["source"]: h for h in store.source_health()}
    enabled = {
        "steam": cfg.steam.enabled, "epic": cfg.epic.enabled, "roblox": cfg.roblox.enabled, "youtube": cfg.youtube.enabled,
    }
    for source, label in SOURCE_LABELS.items():
        if not enabled[source]:
            out.append(f"  {label}: kapalı")
            continue
        mine = [o for o in outages if o["source"] == source]
        total = sum(min(o["ended_at"] or now, now) - max(o["started_at"], since) for o in mine)
        h = health.get(source)
        state = "✅ çalışıyor" if h and h["last_ok"] else "henüz veri yok"
        if any(o["ended_at"] is None for o in mine):
            state = f"⚠️ şu an kesintide ({mine[-1]['reason']})"
        elif h and h["consecutive_failures"]:
            state = f"⚠️ {h['consecutive_failures']} ardışık hata"
        line = f"  {label}: {state}"
        if mine:
            line += f" · {len(mine)} kesinti, toplam {human_duration(total)}"
        elif h and h["last_ok"]:
            line += f" · son başarılı: {_when(h['last_ok'])}"
        out.append(line)
    out.append("")

    # ---- karar dağılımı ve öne çıkanlar
    decisions = store.stats()["decisions"]
    unknown = store._one("SELECT COUNT(*) AS c FROM decisions WHERE roblox_status='unknown'")["c"]
    out.append("KARARLAR (şu an): " + (", ".join(f"{k} {v}" for k, v in decisions.items()) or "yok"))
    if unknown:
        out.append(f"  {unknown} oyun Roblox doğrulaması olmadan değerlendirildi; Roblox'a ulaşılınca otomatik doğrulanır")
    top = [a for a in real if a["type"] in ("opportunity", "rising_trend", "upcoming_trailer", "first_clone")]
    if top:
        out += ["", "ÖNE ÇIKANLAR"]
        seen = set()
        for a in reversed(top):
            if a["title"] in seen:
                continue
            seen.add(a["title"])
            out.append(f"  {_when(a['ts'])}  {a['title']}")
            if len(seen) >= 8:
                break

    # ---- dikkat edilecekler
    notes: list[str] = []
    span = max(1.0, days * DAY)
    for source in ("roblox", "steam", "epic", "youtube"):
        mine = [o for o in outages if o["source"] == source]
        total = sum(min(o["ended_at"] or now, now) - max(o["started_at"], since) for o in mine)
        if enabled[source] and total / span > 0.2:
            hint = " VPN'i sürekli açık tut ya da ağ ayarını kontrol et." if source == "roblox" else " İnternet bağlantısını kontrol et."
            notes.append(f"{SOURCE_LABELS[source]} sürenin %{round(100 * total / span)}'inde ulaşılamadı.{hint}")
    repeated = Counter(a["game_key"] for a in real if a["game_key"] and a["type"] not in ("digest", "system"))
    noisy = [k for k, n in repeated.items() if n >= 4]
    if noisy:
        notes.append(f"{len(noisy)} oyun için 4'ten fazla bildirim geldi; çok gürültülü olabilir (notifications.cooldown_hours artırılabilir).")
    if not real and store.stats()["games"]:
        notes.append("Veri toplanıyor ama hiç bildirim üretilmedi: eşikler yüksek olabilir ya da bildirim kanalı kapalı.")
    youtube = health.get("youtube")
    if cfg.youtube.enabled and (not youtube or not youtube["last_ok"] or now - youtube["last_ok"] > 6 * HOUR):
        notes.append("YouTube fragman verisi 6 saatten uzun süredir güncellenmedi.")
    if notes:
        out += ["", "DİKKAT"] + [f"  • {n}" for n in notes]
    return "\n".join(out)
