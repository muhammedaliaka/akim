"""Platforma bağlı yardımcılar: Windows konsolu, uyku engelleme, .env okuma, kapatma sinyalleri, yerel ağ adresi.

Hepsi yalnızca standart kütüphane kullanır ve her platformda (Linux, macOS, Windows, Termux) güvenle içe aktarılır;
Windows'a özgü çağrılar yalnızca Windows'ta çalışır.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import socket
import sys
from collections.abc import MutableMapping
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def is_android() -> bool:
    """Termux gibi Android kabuklarında True (Android çekirdeği Linux olarak görünür)."""
    return bool(os.environ.get("TERMUX_VERSION")) or ("ANDROID_ROOT" in os.environ and "ANDROID_DATA" in os.environ)


# ---------------------------------------------------------------------- konsol
def _enable_windows_vt() -> bool:
    """Windows 10+ konsolunda ANSI renk kodlarını açar."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def setup_console() -> bool:
    """Çıktıyı UTF-8 yapar (Türkçe karakter ve emoji Windows'ta cp1254/cp437 ile çöker).

    Konsolsuz çalışmada (pythonw.exe, Görev Zamanlayıcı) ``sys.stdout`` None olabilir; yazmalar sessizce yutulsun.
    ANSI renk kullanılabiliyorsa True döner.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        reconfigure = getattr(getattr(sys, name), "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
        return False
    return _enable_windows_vt() if IS_WINDOWS else True


def has_console() -> bool:
    """Gerçek bir konsola (terminal) bağlı mıyız? pythonw/Görev Zamanlayıcı'da False."""
    return sys.__stderr__ is not None


# ---------------------------------------------------------------------- uyku
def keep_awake(enable: bool = True) -> bool:
    """Windows'ta bilgisayarın boşta uykuya geçmesini engeller (ekran kapanabilir).

    Kapak kapatma ve elle uyku bu ayarı aşar. Çağıran iş parçacığı yaşadığı sürece geçerlidir (ana iş parçacığı).
    Diğer platformlarda yapılacak bir şey yoktur; False döner.
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        flags = es_continuous | (es_system_required if enable else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))  # type: ignore[attr-defined]
    except Exception:
        return False


# ---------------------------------------------------------------------- .env
def parse_dotenv(text: str) -> dict[str, str]:
    """Basit .env ayrıştırıcı: ``KEY=değer``, ``export KEY=değer``, tırnaklı değerler, ``#`` yorumları."""
    out: dict[str, str] = {}
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        out[key] = value
    return out


def load_dotenv(paths: list[Path], environ: MutableMapping[str, str] | None = None) -> list[Path]:
    """Var olan .env dosyalarını okuyup ortama ekler. Zaten tanımlı ve boş olmayan değişkenlerin üzerine yazmaz.

    Boş değerler (``AKIM_NTFY_TOPIC=``) atlanır; böylece şablon dosyası olduğu gibi kopyalanabilir.
    Yüklenen dosyaların listesini döndürür. İlk verilen yol önceliklidir.
    """
    env = os.environ if environ is None else environ
    loaded: list[Path] = []
    for path in paths:
        try:
            if not path.is_file():
                continue
            values = parse_dotenv(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        for key, value in values.items():
            if value and not env.get(key):
                env[key] = value
        loaded.append(path)
    return loaded


# ---------------------------------------------------------------------- kapatma sinyalleri
def install_stop_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """SIGINT/SIGTERM (Windows'ta Ctrl+C ve Ctrl+Break) gelince ``stop`` olayını tetikler.

    Windows olay döngüsü ``add_signal_handler`` desteklemez; orada ``signal.signal`` ile aynı iş yapılır ve
    döngü ``call_soon_threadsafe`` ile uyandırılır. Böylece temizlik kodu (bağlantıları kapatma, veritabanı) çalışır.
    """
    names = ["SIGINT", "SIGTERM"] + (["SIGBREAK"] if IS_WINDOWS else [])
    for name in names:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
            except (ValueError, OSError):  # ana iş parçacığı değil
                pass


# ---------------------------------------------------------------------- ağ
def lan_addresses() -> list[str]:
    """Telefonun aynı Wi-Fi ağından panele bağlanabileceği yerel IPv4 adresleri (en olası olan başta)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET; paket gönderilmez, yalnızca çıkış arayüzü seçilir
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith(("127.", "169.254.", "0."))]
