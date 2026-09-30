"""Platforma bağlı davranışlar: konsol kodlaması, .env, yol çözümleme, kapatma sinyalleri, log renkleri."""

import asyncio
import io
import logging
import os
import signal
import socket
import sys
from pathlib import Path

import pytest

from akim import runtime
from akim.__main__ import ColorFormatter, setup_logging
from akim.config import load_config
from akim.models import Alert, Priority
from akim.notify.channels import ConsoleChannel, NtfyChannel, _clip_bytes
from akim.config import ChannelConfig


# ------------------------------------------------------------------ .env
def test_parse_dotenv_handles_quotes_comments_bom_and_export():
    text = (
        "﻿# yorum\n"
        "AKIM_NTFY_TOPIC=abc-123 # satır sonu yorumu\n"
        'AKIM_TELEGRAM_BOT_TOKEN="123:ABC#def"\n'
        "export AKIM_WEB_TOKEN='gizli deger'\n"
        "BOS=\n"
        "gecersiz anahtar=1\n"
        "1BASLAR=2\n"
        "SATIR_YOK\n"
    )
    assert runtime.parse_dotenv(text) == {
        "AKIM_NTFY_TOPIC": "abc-123",
        "AKIM_TELEGRAM_BOT_TOKEN": "123:ABC#def",  # tırnak içindeki # yorum değildir
        "AKIM_WEB_TOKEN": "gizli deger",
        "BOS": "",
    }


def test_load_dotenv_never_overrides_real_values_and_skips_blanks(tmp_path):
    first = tmp_path / "a.env"
    second = tmp_path / "b.env"
    first.write_text("A=ilk\nB=ilk\nBOS=\n", encoding="utf-8")
    second.write_text("A=ikinci\nC=ikinci\n", encoding="utf-8")
    env = {"B": "gercek"}
    loaded = runtime.load_dotenv([first, tmp_path / "yok.env", second], env)
    assert loaded == [first, second]
    assert env == {"A": "ilk", "B": "gercek", "C": "ikinci"}  # ilk dosya öncelikli, gerçek ortam korunur, boş atlanır


def test_load_dotenv_fills_empty_existing_variable(tmp_path):
    # Docker env_file bazen boş değişken tanımlar; gerçek değer .env'de varsa boşun üzerine yazılmalı
    p = tmp_path / ".env"
    p.write_text("X=dolu\n", encoding="utf-8")
    env = {"X": ""}
    assert runtime.load_dotenv([p], env) == [p]
    assert env == {"X": "dolu"}


# ------------------------------------------------------------------ config: .env ve yol çözümleme
def test_config_reads_dotenv_next_to_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AKIM_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("BENIM_CHAT", raising=False)
    elsewhere = tmp_path / "baska"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # Görev Zamanlayıcı/systemd çalışma klasörünü kendi seçer
    home = tmp_path / "kurulum"
    home.mkdir()
    (home / ".env").write_text("AKIM_NTFY_TOPIC=telefonum\nBENIM_CHAT=77\n", encoding="utf-8")
    (home / "config.yaml").write_text(
        "notifications:\n  channels:\n    telegram: {enabled: true, bot_token: x, chat_id: '${BENIM_CHAT:-1}'}\n",
        encoding="utf-8",
    )
    cfg = load_config(home / "config.yaml")
    assert cfg.notifications.channels["ntfy"].topic == "telefonum"
    assert cfg.notifications.channels["telegram"].chat_id == "77"
    monkeypatch.delenv("AKIM_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("BENIM_CHAT", raising=False)


def test_empty_env_var_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BOS_DEGISKEN", "")
    p = tmp_path / "c.yaml"
    p.write_text("notifications:\n  channels:\n    telegram: {enabled: true, bot_token: x, chat_id: '${BOS_DEGISKEN:-42}'}\n")
    assert load_config(p).notifications.channels["telegram"].chat_id == "42"


def test_relative_paths_resolve_against_config_dir_not_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("AKIM_DATA_DIR", raising=False)
    monkeypatch.delenv("AKIM_LOG_FILE", raising=False)
    cwd = tmp_path / "system32"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    home = tmp_path / "akim"
    home.mkdir()
    (home / "config.yaml").write_text("general: {data_dir: ./veri, log_file: loglar/akim.log}\n", encoding="utf-8")
    cfg = load_config(home / "config.yaml")
    assert cfg.db_path == home / "veri" / "akim.db"
    assert Path(cfg.general.log_file) == home / "loglar" / "akim.log"

    (home / "mutlak.yaml").write_text(f"general: {{data_dir: '{tmp_path / 'mutlak'}'}}\n", encoding="utf-8")
    assert load_config(home / "mutlak.yaml").db_path == tmp_path / "mutlak" / "akim.db"


def test_config_file_with_bom_loads(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_bytes("﻿general: {country: tr}\n".encode("utf-8"))  # Not Defteri'nin 'UTF-8 (BOM)' kaydı
    assert load_config(p).general.country == "tr"


# ------------------------------------------------------------------ konsol
def test_setup_console_survives_legacy_windows_codepage(monkeypatch):
    legacy_out = io.TextIOWrapper(io.BytesIO(), encoding="cp1254", errors="strict")
    legacy_err = io.TextIOWrapper(io.BytesIO(), encoding="cp437", errors="strict")
    with pytest.raises(UnicodeEncodeError):
        legacy_out.write("✅")  # sorunun kendisi: cp1254 emojiyi yazamaz
    monkeypatch.setattr(sys, "stdout", legacy_out)
    monkeypatch.setattr(sys, "stderr", legacy_err)
    assert runtime.setup_console() is False  # tty değil -> renk yok
    print("✅ Akım İ ğ ş", file=sys.stdout)
    print("⚠️ çöker mi?", file=sys.stderr)
    sys.stdout.flush()
    assert "Akım İ ğ ş".encode("utf-8") in legacy_out.buffer.getvalue()


def test_setup_console_without_console_streams(monkeypatch):
    """pythonw.exe / Görev Zamanlayıcı: sys.stdout ve sys.stderr None olabilir."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert runtime.setup_console() is False
        print("görünmez çıktı")  # hata vermemeli
        logging.getLogger("t").warning("yutulur")
    finally:
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name)
            if stream is not None:
                stream.close()


def test_no_color_env_disables_color(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setattr(sys, "stderr", Tty())
    monkeypatch.setenv("NO_COLOR", "1")
    assert runtime.setup_console() is False


# ------------------------------------------------------------------ uyku, android, ağ
@pytest.mark.skipif(os.name == "nt", reason="Windows'ta gerçek çağrı aşağıda test edilir")
def test_keep_awake_is_noop_outside_windows():
    assert runtime.keep_awake(True) is False and runtime.keep_awake(False) is False


@pytest.mark.skipif(os.name != "nt", reason="yalnızca Windows")
def test_keep_awake_on_windows():
    assert runtime.keep_awake(True) is True
    assert runtime.keep_awake(False) is True  # ES_CONTINUOUS tek başına bayrakları sıfırlar


def test_is_android(monkeypatch):
    for k in ("TERMUX_VERSION", "ANDROID_ROOT", "ANDROID_DATA"):
        monkeypatch.delenv(k, raising=False)
    assert runtime.is_android() is False
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    assert runtime.is_android() is True


def test_lan_addresses_filters_loopback_and_tolerates_no_network(monkeypatch):
    ips = runtime.lan_addresses()
    assert all(isinstance(ip, str) and not ip.startswith(("127.", "169.254.")) for ip in ips)

    def boom(*a, **k):
        raise OSError("ağ yok")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert runtime.lan_addresses() == []


# ------------------------------------------------------------------ kapatma sinyalleri
def _restore_signals():
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}

    def restore():
        for s, h in saved.items():
            signal.signal(s, h)

    return restore


def test_stop_handler_via_event_loop():
    restore = _restore_signals()

    async def run():
        stop = asyncio.Event()
        runtime.install_stop_handlers(asyncio.get_running_loop(), stop)
        signal.raise_signal(signal.SIGINT if os.name == "nt" else signal.SIGTERM)
        await asyncio.wait_for(stop.wait(), 3)

    try:
        asyncio.run(run())
    finally:
        restore()


def test_stop_handler_fallback_used_on_windows_style_loops(monkeypatch):
    """Windows olay döngüsü add_signal_handler desteklemez (NotImplementedError); signal.signal yoluna düşer."""
    restore = _restore_signals()

    async def run():
        loop = asyncio.get_running_loop()

        def unsupported(*a, **k):
            raise NotImplementedError

        monkeypatch.setattr(loop, "add_signal_handler", unsupported)
        stop = asyncio.Event()
        runtime.install_stop_handlers(loop, stop)
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(stop.wait(), 3)

    try:
        asyncio.run(run())
    finally:
        restore()


# ------------------------------------------------------------------ log renkleri ve bildirim kanalı
@pytest.fixture
def clean_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield root
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)


def test_console_channel_colors_only_the_console_not_the_log_file(tmp_path, clean_root_logger):
    log_file = tmp_path / "alt" / "akim.log"
    setup_logging("INFO", str(log_file), color=True)
    alert = Alert(type="opportunity", priority=Priority.CRITICAL, title="Yeni fırsat ✅", body="Roblox'ta yok")
    asyncio.run(ConsoleChannel(ChannelConfig()).send(alert))
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "Yeni fırsat ✅" in text and "\x1b[" not in text  # dosya temiz

    record = logging.LogRecord("akim.alert", logging.INFO, "", 0, "merhaba", None, None)
    record.akim_color = "\033[31;1m"
    assert ColorFormatter("%(message)s").format(record) == "\033[31;1mmerhaba\033[0m"
    plain = logging.LogRecord("akim", logging.INFO, "", 0, "düz", None, None)
    assert ColorFormatter("%(message)s").format(plain) == "düz"


def test_ntfy_body_is_clipped_below_the_attachment_limit():
    assert _clip_bytes("kısa", 100) == "kısa"
    clipped = _clip_bytes("ğ" * 5000, NtfyChannel.MAX_BODY)
    assert len(clipped.encode("utf-8")) <= NtfyChannel.MAX_BODY and clipped.endswith("...")
    clipped.encode("utf-8").decode("utf-8")  # kesilen çok baytlı karakter bozuk kalmaz
