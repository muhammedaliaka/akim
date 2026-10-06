"""Arkadaş daveti (ntfy) ve sade rapor."""

import asyncio
import time

import pytest

from akim.__main__ import main
from akim.config import ChannelConfig, Config
from akim.invite import build_invite, ntfy_target
from akim.models import Alert, Priority
from akim.report import build_report
from akim.storage import Storage
from tests.test_engine import build

DAY = 86400


def cfg_with(**channels):
    cfg = Config()
    cfg.notifications.channels.update(channels)
    return cfg


# ---------------------------------------------------------------------- davet
def test_invite_is_short_and_has_every_step():
    text = build_invite("https://ntfy.sh", "akim-4f9c2a7e1b")
    assert "akim-4f9c2a7e1b" in text and "ntfy://ntfy.sh/akim-4f9c2a7e1b" in text
    for must in ("Play Store", "F-Droid", "App Store", "Abone ol", "Bildirim iznini", "Kısıtlama yok", "şifre gibidir"):
        assert must in text
    assert len(text.splitlines()) <= 18  # çok kısa: tek ekranda okunur
    assert "olduğu gibi bırak" in text  # varsayılan sunucuda kafa karıştıran adım yok


def test_invite_for_self_hosted_server_names_the_server():
    text = build_invite("https://ntfy.example.org/", "gizli-konu")
    assert "https://ntfy.example.org/" in text and "ntfy://ntfy.example.org/gizli-konu" in text
    assert "olduğu gibi bırak" not in text


def test_ntfy_target_picks_enabled_channel_with_topic():
    assert ntfy_target(Config()) is None
    assert ntfy_target(cfg_with(ntfy=ChannelConfig(enabled=True, topic=""))) is None
    assert ntfy_target(cfg_with(ntfy=ChannelConfig(enabled=False, topic="x"))) is None
    cfg = cfg_with(**{"ntfy:aile": ChannelConfig(enabled=True, topic="aile-konusu", server="https://s.example/")})
    assert ntfy_target(cfg) == ("https://s.example", "aile-konusu")


def test_cli_invite(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AKIM_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("AKIM_CONFIG", raising=False)
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("general: {data_dir: ./d}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as info:
        main(["-c", str(cfgfile), "invite"])
    assert info.value.code == 1 and "AKIM_NTFY_TOPIC" in capsys.readouterr().err

    monkeypatch.setenv("AKIM_NTFY_TOPIC", "akim-demo-7f3a")
    main(["-c", str(cfgfile), "invite"])
    out = capsys.readouterr().out
    assert "akim-demo-7f3a" in out and "Not (yalnızca sana)" in out and "mesaj da gönderebilir" in out


# ---------------------------------------------------------------------- rapor
def seeded_store():
    store = Storage(":memory:")
    now = time.time()

    def alert(type_, prio, title, key=None, ago=3600):
        a = Alert(type=type_, priority=prio, title=title, body="x", game_key=key, ts=now - ago)
        store.add_alert(a)

    alert("opportunity", Priority.CRITICAL, "FIRSAT: Moss Diver", "steam:1", 2 * DAY)
    alert("upcoming_trailer", Priority.MEDIUM, "🎬 Çıkmamış oyun ses getiriyor: Warhammer Survivors", None, DAY)
    alert("rising_unverified", Priority.MEDIUM, "📈 Yükseliyor: Tiny Pond", "steam:2", 3 * 3600)
    alert("digest", Priority.MEDIUM, "Akım özeti", None, 100)
    for i in range(5):
        alert("rank_surge", Priority.HIGH, f"🟡 Gürültücü: #{30 - i} → #{20 - i}", "steam:9", 4000 + i)
    # Roblox: son 3 günün %37'si kesintide (VPN): 27 saat
    store.open_outage("roblox", now - 3 * DAY, "adres çözülemedi (internet yok ya da site engelli)")
    store.close_outage("roblox", now - 2 * DAY + 3 * 3600)
    store.source_failed("roblox", "x", now)
    store.source_ok("steam", now)
    return store, now


def test_report_summarises_alerts_outages_and_warnings():
    store, now = seeded_store()
    cfg = Config()
    cfg.youtube.enabled = False
    text = build_report(store, cfg, days=3, now=now)
    assert "BİLDİRİMLER: 9 adet" in text and "fırsat 1" in text and "yaklaşan oyun (fragman) 1" in text
    assert "yükseliyor (Roblox'suz) 1" in text and "kritik 1" in text
    assert "Roblox: ⚠️" in text and "1 kesinti, toplam 1 gün 3 sa" in text
    assert "Steam: ✅ çalışıyor" in text and "YouTube: kapalı" in text
    assert "ÖNE ÇIKANLAR" in text and "FIRSAT: Moss Diver" in text
    assert "VPN'i sürekli açık tut" in text  # Roblox 3 günlük pencerede %20'den fazla kesinti
    assert "1 oyun için 4'ten fazla bildirim" in text
    assert "Traceback" not in text and "Exception" not in text


def test_report_flags_ongoing_outage_and_unverified_games():
    store, now = seeded_store()
    store.open_outage("steam", now - 3600, "sunucu zamanında yanıt vermedi")
    store.db.execute(
        "INSERT INTO games(key, store, store_id, title) VALUES('steam:2','steam','2','Tiny Pond')"
    )
    store.save_decision("steam:2", {"decision": "watch", "score": .6, "indie": .9, "momentum": .5, "saturation": .5,
                                    "roblox_status": "unknown", "reasons": []}, now)
    cfg = Config()
    cfg.youtube.enabled = False
    text = build_report(store, cfg, days=7, now=now)
    assert "Steam: ⚠️ şu an kesintide (sunucu zamanında yanıt vermedi)" in text
    assert "1 oyun Roblox doğrulaması olmadan" in text


def test_report_on_empty_database_is_honest():
    text = build_report(Storage(":memory:"), Config(), days=2)
    assert "BİLDİRİMLER: 0 adet" in text and "henüz veri yok" in text


def test_telegram_report_command_and_cli(tmp_path, capsys):
    engine, _ = build()
    reply = asyncio.run(engine.command("rapor", "3"))
    assert "Akım raporu — son 3 gün" in reply and "<" not in reply  # HTML kaçışlı
    assert "/rapor" in asyncio.run(engine.command("yardim", ""))

    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("general: {data_dir: ./d}\n", encoding="utf-8")
    main(["-c", str(cfgfile), "report", "--days", "1"])
    assert "Akım raporu — son 1 gün" in capsys.readouterr().out


# ---------------------------------------------------------------------- konu adı gücü
@pytest.mark.parametrize(
    "topic, weak",
    [
        ("akim", True), ("test", True), ("oyunlar-bildirim", False), ("abcdefghijkl", True), ("aaaaaaaaaaaa1", True),
        ("akim-4f9c2a7e1b", False), ("tamamen-rastgele-9x7k2", False), ("kisa-1", True),
    ],
)
def test_topic_weakness(topic, weak):
    from akim.invite import topic_weakness

    assert bool(topic_weakness(topic)) is weak


def test_suggested_topic_is_strong_and_unique():
    from akim.invite import suggest_topic, topic_weakness

    a, b = suggest_topic(), suggest_topic()
    assert a != b and a.startswith("akim-") and topic_weakness(a) is None


def test_cli_invite_warns_about_weak_topic(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AKIM_CONFIG", raising=False)
    monkeypatch.setenv("AKIM_NTFY_TOPIC", "akim")
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("general: {data_dir: ./d}\n", encoding="utf-8")
    main(["-c", str(cfgfile), "invite"])
    captured = capsys.readouterr()
    assert "çok kısa" in captured.err and "AKIM_NTFY_TOPIC=akim-" in captured.err


def test_web_host_env_shortcut(monkeypatch, tmp_path):
    from akim.config import load_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKIM_CONFIG", raising=False)
    assert load_config().web.host == "127.0.0.1"  # varsayılan: yalnızca bu cihaz
    monkeypatch.setenv("AKIM_WEB_HOST", "0.0.0.0")
    assert load_config().web.host == "0.0.0.0"
