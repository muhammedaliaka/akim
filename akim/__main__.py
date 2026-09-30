"""Komut satırı arayüzü.

    python -m akim run                 # 7/24 izleme (varsayılan)
    python -m akim once --limit 20     # tek tur: listeleri çek, ilk 20 adayı Roblox'ta tara, tabloyu yaz
    python -m akim check "Schedule I"  # bir oyunu anında Roblox'ta kontrol et
    python -m akim top                 # veritabanındaki güncel fırsat tablosu
    python -m akim test-notify         # tüm kanallara test bildirimi gönder
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, load_config
from .engine import DECISION_EMOJI, Engine, _fmt_int
from .events import EventBus
from .http import HttpClient
from .models import Decision, RobloxStatus
from .notify import Notifier, build_channels
from .runtime import has_console, install_stop_handlers, keep_awake, lan_addresses, setup_console
from .storage import Storage

log = logging.getLogger("akim")


LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


class ColorFormatter(logging.Formatter):
    """Bildirim kanalının işaretlediği kayıtları renklendirir (yalnızca konsol işleyicisinde kullanılır)."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        color = getattr(record, "akim_color", None)
        return f"{color}{text}\033[0m" if color else text


def setup_logging(level: str, log_file: str = "", color: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(ColorFormatter(LOG_FORMAT, LOG_DATEFMT) if color else logging.Formatter(LOG_FORMAT, LOG_DATEFMT))
    root.addHandler(console)
    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATEFMT))
            root.addHandler(fh)
        except OSError as exc:
            root.warning("Log dosyası açılamadı (%s): %s", log_file, exc)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


class App:
    """Tüm bileşenleri bir araya getirir ve düzgün kapatır."""

    def __init__(self, cfg: Config, *, notify: bool = True):
        self.cfg = cfg
        self.store = Storage(cfg.db_path)
        self.bus = EventBus()
        self.http = HttpClient()
        self.engine = Engine(cfg, self.store, self.http, self.bus)
        channels = build_channels(cfg.notifications, self.http, self.engine.command) if notify else []
        self.notifier = Notifier(cfg.notifications, self.store, self.bus, channels)
        self.engine.notifier = self.notifier

    async def close(self) -> None:
        await self.notifier.stop()
        await self.http.close()
        self.store.close()


async def cmd_run(cfg: Config) -> None:
    app = App(cfg)
    runner = None
    engine_task: asyncio.Task | None = None
    stop = asyncio.Event()
    awake = False
    try:
        install_stop_handlers(asyncio.get_running_loop(), stop)
        await app.notifier.start()
        if cfg.general.keep_awake and keep_awake(True):
            awake = True
            log.info("Windows uyku modu engellendi (ekran kapanabilir; kapağı kapatırsan bilgisayar yine uyur)")
        if cfg.web.enabled:
            from .web.server import start_web

            try:
                runner = await start_web(app.engine, cfg.web)
            except OSError as exc:
                # Bildirimler panelden önemli: port doluysa (Windows'ta 8080 sık kullanılır) panelsiz sürdür
                log.error("Web paneli başlatılamadı (port %d dolu olabilir; web.port ile değiştir): %s", cfg.web.port, exc)

        log.info("Akım %s başladı (veri: %s)", __version__, cfg.db_path)
        engine_task = asyncio.create_task(app.engine.run_forever(), name="engine")
        stop_task = asyncio.create_task(stop.wait(), name="stop")
        done, _ = await asyncio.wait({engine_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        stop_task.cancel()
        if engine_task in done and not engine_task.cancelled() and engine_task.exception():
            log.error("Motor beklenmedik şekilde durdu", exc_info=engine_task.exception())
            raise SystemExit(1)  # bir denetleyici (Görev Zamanlayıcı, systemd, run.cmd) yeniden başlatsın
    finally:
        log.info("Kapatılıyor…")
        if engine_task:
            engine_task.cancel()
            await asyncio.gather(engine_task, return_exceptions=True)
        if runner:
            await runner.cleanup()
        await app.close()
        if awake:
            keep_awake(False)


def _print_board(rows: list[dict]) -> None:
    if not rows:
        print("Kayıt yok.")
        return
    print(f"{'':2} {'Oyun':38} {'Karar':13} {'Roblox':20} {'Sıra':>5} {'Skor':>5}  B/M/D")
    for r in rows:
        d = Decision(r["decision"])
        rank = f"#{r['best_rank']}" if r.get("best_rank") else "-"
        print(
            f"{DECISION_EMOJI[d]} {r['title'][:38]:38} {d.label:13} {RobloxStatus(r['roblox_status']).label:20} "
            f"{rank:>5} {r['score']:5.2f}  {r['indie']:.2f}/{r['momentum']:.2f}/{r['saturation']:.2f}"
        )


async def cmd_once(cfg: Config, limit: int, notify: bool) -> None:
    app = App(cfg, notify=notify)
    try:
        if notify:
            await app.notifier.start()
        e = app.engine
        e.bootstrapping = False
        if cfg.steam.enabled:
            await e.poll_steam()
        if cfg.epic.enabled:
            await e.poll_epic()
        due = e._due_games()[:limit]
        print(f"\n{len(due)} bağımsız aday Roblox'ta taranıyor…")
        for i, row in enumerate(due, 1):
            try:
                await e.scan_roblox(row["key"])
            except Exception as exc:
                print(f"  ! {row['title']}: {exc}")
            print(f"  [{i}/{len(due)}] {row['title']}")
        print()
        _print_board(e.board(limit=max(limit, 30)))
    finally:
        await app.close()


async def cmd_check(cfg: Config, title: str) -> None:
    app = App(cfg, notify=False)
    try:
        r = await app.engine.adhoc_check(title)
    finally:
        await app.close()
    print(
        f"\n{r['resolved_title']} → {r['status_label']} ({r['clone_count']} klon, {r['similar_count']} benzer oynanış, "
        f"doygunluk {r['saturation']:.2f}, ağırlıklı {r['total_playing']} anlık oyuncu)"
    )
    print(f"Bağlam: {r['context_source']}" + (f" · etiketler: {', '.join(r['tags'][:6])}" if r["tags"] else ""))
    extras = ["Laya"] if r.get("laya") else []
    print(f"Aramalar: {', '.join(r['queries'])}" + (f" · açık: {', '.join(extras)}" if extras else ""))
    if r["generic"]:
        print("⚠️  Genel bir isim; eşleşmeleri elle doğrula.")
    if r["matches"]:
        print("\nRoblox'taki benzerleri (% benzerlik):")
        for m in r["matches"]:
            kind = "KLON  " if m["kind"] == "clone" else "benzer"
            print(f"  %{round(m['similarity'] * 100):3} {kind} {m['name'][:42]:42} {_fmt_int(m['playing']):>6} oyuncu {_fmt_int(m['visits']):>7} ziyaret")
            print(f"              {m['reason']}")
            print(f"              {m['url']}")
    else:
        print("\nRoblox'ta benzer oynanışa sahip oyun bulunamadı.")
    if r["related"]:
        print("\nEn yakın diğer adaylar (eşleşme sayılmadı): " + ", ".join(
            f"{x['name'][:28]} (%{round(x['similarity'] * 100)})" for x in r["related"]))


async def cmd_test_notify(cfg: Config) -> None:
    app = App(cfg)
    try:
        results = await app.notifier.test()
    finally:
        await app.close()
    for name, ok in results.items():
        print(f"{'✅' if ok else '❌'} {name}")
    if not all(results.values()):
        sys.exit(1)


def cmd_top(cfg: Config, limit: int) -> None:
    store = Storage(cfg.db_path)
    now = time.time()
    rows = store.board(None, limit, since=now - cfg.general.track_days * 86400, fresh_since=now - 3 * 3600)
    _print_board(rows)
    store.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="akim", description="Steam/Epic → Roblox 7/24 akım izleme sistemi")
    parser.add_argument("-c", "--config", help="config.yaml yolu (varsayılan: AKIM_CONFIG veya ./config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="ayrıntılı log")
    parser.add_argument("--version", action="version", version=f"akim {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="7/24 izlemeyi başlat")
    p_once = sub.add_parser("once", help="tek tur tarama yap ve sonuçları yazdır")
    p_once.add_argument("--limit", type=int, default=25, help="Roblox'ta taranacak en fazla aday")
    p_once.add_argument("--notify", action="store_true", help="bildirim de gönder")
    p_check = sub.add_parser("check", help="bir oyunu anında Roblox'ta kontrol et")
    p_check.add_argument("title", nargs="+")
    p_top = sub.add_parser("top", help="güncel fırsat tablosunu yazdır")
    p_top.add_argument("--limit", type=int, default=30)
    sub.add_parser("test-notify", help="tüm kanallara test bildirimi gönder")
    args = parser.parse_args(argv)

    console = has_console()  # setup_console() konsolsuz akışları değiştirmeden önce ölç
    color = setup_console()
    cfg = load_config(args.config)
    log_file = cfg.general.log_file
    if not log_file and not console:
        log_file = str(Path(cfg.general.data_dir) / "akim.log")  # pythonw/Görev Zamanlayıcı: log görünmez olmasın
    setup_logging("DEBUG" if args.verbose else cfg.general.log_level, log_file, color)
    command = args.command or "run"
    try:
        if command == "run":
            asyncio.run(cmd_run(cfg))
        elif command == "once":
            asyncio.run(cmd_once(cfg, args.limit, args.notify))
        elif command == "check":
            asyncio.run(cmd_check(cfg, " ".join(args.title)))
        elif command == "top":
            cmd_top(cfg, args.limit)
        elif command == "test-notify":
            asyncio.run(cmd_test_notify(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
