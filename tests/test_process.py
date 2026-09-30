"""Gerçek süreç testi: sunucuyu ayrı bir işlem olarak başlat, sağlık ucunu yokla, kapatma sinyali gönder.

Linux/macOS'ta SIGTERM, Windows'ta CTRL_BREAK_EVENT kullanılır; ikisinde de temiz kapanış (çıkış kodu 0,
"Kapatılıyor" logu, veritabanının config klasöründe olması) doğrulanır. CI bunu Windows'ta da çalıştırır.
"""

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(port: int, proc: subprocess.Popen, timeout: float = 40) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"süreç erken çıktı (kod {proc.returncode})")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.3)
    raise AssertionError("sağlık ucu zamanında yanıt vermedi")


def start(tmp_path, port, *extra_yaml, cwd):
    (tmp_path / "config.yaml").write_text(
        "general: {data_dir: ./veri, log_file: akim.log, keep_awake: true}\n"
        "steam: {enabled: false}\nepic: {enabled: false}\nroblox: {enabled: false}\n"
        "notifications: {startup_summary: false}\n"
        f"web: {{host: 127.0.0.1, port: {port}}}\n" + "".join(extra_yaml),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("AKIM_NTFY_TOPIC=test-konusu\n", encoding="utf-8")
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    env = {k: v for k, v in os.environ.items() if not k.startswith("AKIM_")}
    return subprocess.Popen(
        [sys.executable, "-m", "akim", "-c", str(tmp_path / "config.yaml"), "run"],
        cwd=str(cwd), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
    )


def stop(proc: subprocess.Popen) -> int:
    proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
    try:
        return proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("süreç kapatma sinyaline yanıt vermedi")


def test_server_runs_serves_pwa_and_stops_cleanly(tmp_path):
    cwd = tmp_path / "baska_klasor"  # çalışma klasörü config'inkinden farklı (Görev Zamanlayıcı: System32)
    cwd.mkdir()
    port = free_port()
    proc = start(tmp_path, port, cwd=cwd)
    try:
        wait_health(port, proc)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/manifest.webmanifest", timeout=5) as r:
            assert r.status == 200
        code = stop(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert code == 0, "graceful kapanış çıkış kodu 0 olmalı (run.cmd/systemd yeniden başlatmasın)"
    log = (tmp_path / "akim.log").read_text(encoding="utf-8")
    assert "Kapatılıyor" in log and "ntfy" in log, log  # .env okundu, kapatma temizlik koduna ulaştı
    assert (tmp_path / "veri" / "akim.db").is_file(), "veri config klasörüne yazılmalı"
    assert not (cwd / "data").exists() and not (cwd / "veri").exists(), "çalışma klasörü kirlenmemeli"


def test_port_conflict_degrades_to_headless_instead_of_crashing(tmp_path):
    """Windows'ta 8080 sık dolu olur: bildirimler panelden önemli, sistem panelsiz sürmeli."""
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        proc = start(tmp_path, port, cwd=tmp_path)
        try:
            log_file = tmp_path / "akim.log"
            deadline = time.time() + 40
            while time.time() < deadline and not (log_file.exists() and "başladı" in log_file.read_text(encoding="utf-8")):
                assert proc.poll() is None, "port çakışmasında süreç çökmemeli"
                time.sleep(0.3)
            code = stop(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
    log = (tmp_path / "akim.log").read_text(encoding="utf-8")
    assert code == 0 and "Web paneli başlatılamadı" in log and "başladı" in log
