# Akım — geliştirici devir notları (CLAUDE.md)

Bu dosya, projeyi daha önce geliştiren oturumun bilgisini yeni bir oturuma (yerel ya da bulut) aktarır. Önce bunu oku,
sonra `README.md` ve ilgili `docs/` dosyalarına bak. Burada yazanlar 2026-10-06 itibarıyla doğrulanmıştır; "Doğrulanmadı"
dediklerime güvenme, kendin dene.

## Proje ve sahibi
- **Akım**: Steam ve Epic'teki yükselen **bağımsız** oyunları izler, Roblox'ta klonu/benzeri var mı bakar, karar verir ve
  **anında** bildirir (ntfy, Telegram, Discord, Slack, e-posta, webhook, web paneli). Ayrıca YouTube fragmanlarından
  **henüz çıkmamış** oyunları yakalar. Python 3.10+, aiohttp, PyYAML, SQLite. Depo: `muhammedaliaka/akim`, tek dal
  `ccr-8b3e0004-01mkkx` (varsayılan dal da bu; `main` yok).
- **Sahip Türkçe konuşur.** Konuşma, kod yorumları, bildirim metinleri, belgeler Türkçe. Yeni kullanıcıya dönük her metin Türkçe olmalı.
- Sistemi büyük olasılıkla **Android/Xiaomi (HyperOS) telefonda Termux'ta** çalıştırıyor; bildirimler ntfy ile telefonuna ve
  arkadaşlarına gidiyor. Roblox'a erişmek için **VPN** gerekebiliyor (VPN kopunca sorun çıktı → "Roblox'suz mod").
  Sahibin canlı verisine (`data/akim.db`, loglar, bir haftalık bildirimler) erişimin yok; ancak `akim report --days 7`
  çıktısını yapıştırırsa görürsün.

## Sahibin istekleri ve kurallar (bunlara uy)
1. **Ücretli/kredili hiçbir şey yok.** Claude/Anthropic API doğrulayıcısı bilerek silindi (`llm:` ayarı artık uyarıyla yok sayılır).
   Anahtar, hesap ya da kredi isteyen servis ekleme. Laya (yerel, Apache 2.0) serbest. Barındırma önerisi olarak VPS önerme.
2. **Gerçek davranışı doğrula, tahmin etme.** Dış servis varsayımlarını canlı dene (aşağıda doğrulanmış olgular var). Denemediğini
   "çalışıyor" diye yazma; kullanıcıya neyin doğrulanıp neyin doğrulanmadığını açıkça söyle.
3. **PR açma**, kullanıcı istemedikçe. Dalı commit + push et. Commit mesajı Türkçe ve gerekçeli; sonuna oturumun sistem
   istemindeki ek satırları ekle (önceki commit'lerde `Co-Authored-By: Claude <noreply@anthropic.com>` kullanıldı; model sürümü yazılmadı).
4. Çalışırken: her özellik için test yaz; önemli güvenlik/yanlış-pozitif korumalarını **mutasyonla** doğrula (korumayı kaldır →
   test kırmızıya dönmeli → geri al). README/docs'u koddan ayrı bırakma.
5. Kullanıcıya yanıtlar: kısa, düz Türkçe, ne yaptığın + ne doğrulandı + ne doğrulanmadı.

## Mimari (akım şeması)
```
Steam/Epic listeleri ─► ingest ─► IndieClassifier ─► SQLite ─► momentum
                                          │
                          Roblox taraması (scanner döngüsü) ─► sezgisel kanıt (isim/atıf/oynanış) [+Laya] ─► classify
                                          │                                                      │
                                   RobloxHealth (VPN/engel)                              assess_roblox ─► decide ─► Alert ─► Notifier ─► kanallar
YouTube RSS ─► parse_trailer_title ─► Steam doğrulama ("yakında"?) ─► upcoming tablosu ─► buzz ─► Alert(upcoming_trailer)
```
- `akim/engine.py` (≈1300 satır): tüm döngüler, karar, bildirim üretimi, Roblox sağlık durumu, YouTube işleme, Telegram komutları.
- `akim/analysis/`: `indie.py` (büyük marka/bağımsız), `matching.py` (isim/atıf), `concepts.py` (oynanış kavramları),
  `similarity.py` (kanıt birleştirme, noisy-OR, klon/benzer sınıfı), `scoring.py` (momentum, doygunluk, `decide`),
  `trailers.py` (YouTube başlığı → oyun adı, `buzz_score`), `laya_judge.py` (isteğe bağlı yerel model, varsayılan kapalı).
- `akim/sources/`: `steam.py`, `epic.py`, `roblox.py` (`RobloxUnavailable`, `probe()`), `youtube.py` (RSS).
- `akim/notify/`: `base.py` (`oneline`, `safe_url`), `channels.py`, `telegram.py` (komutlar), `__init__.py` (`Notifier`, cooldown).
- `akim/web/`: `server.py` (güvenlik katmanları), `dashboard.html` (tek dosya PWA), `static/` (manifest, sw.js, simgeler).
- `akim/storage.py` (SQLite şeması + göç), `config.py`, `http.py` (yeniden deneme, **gizli bilgi maskeleme**), `runtime.py`
  (Windows konsol/uyku/`.env`/sinyal), `diagnostics.py` (ham hata → sade Türkçe), `report.py`, `invite.py`, `__main__.py` (CLI).
- `deploy/`: `windows/` (PowerShell + cmd), `termux/` (install.sh, run.sh), `akim.service` (systemd). `docs/`: windows, android-hyperos, termux, arkadaslar.
- `eval/`: Roblox eşleştirme ölçümleri (gerçek etiketli veri; Laya karşılaştırması). Laya hazır halde zayıf çıktı → **varsayılan kapalı**.

CLI: `akim run | once | check | top | test-notify | report | invite`. Telegram: `/firsatlar /kontrol /oyun /yaklasan /rapor /durum /yardim`.

## Önemli tasarım kararları (neden böyle?)
- **Roblox "ulaşılamadı" ≠ "yok".** `RobloxSource._get` ağ/403/5xx/JSON-olmayan/boş yanıtı `RobloxUnavailable`'a çevirir. Sağlıklı Roblox
  anlamsız sorguya bile ~40 oyun döndürür; bu yüzden **hiç sonuç yoksa** `_ensure_roblox_alive()` kanarya yoklaması yapar. Aksi halde
  sahte "Roblox'ta YOK" → sahte FIRSAT çıkardı.
- **`RobloxHealth` durum makinesi** (engine.py): `FAILS_TO_DOWN=2` ardışık hata → kesinti; kesintide tarama ve klon takibi durur;
  `roblox.probe_minutes` (5) aralıkla yoklanır; kesinti `outage_alert_minutes` (10) sürerse **tek** HIGH uyarı; toparlanınca tek LOW bildirim;
  durum `kv` tablosunda saklanır (yeniden başlatmada tekrar uyarmaz). Kesintiler `outages` tablosunda (rapor için).
- **Roblox'suz mod**: `RobloxStatus.UNKNOWN`. Roblox kapalı (`roblox.enabled: false`) ya da kesintideyse `evaluate()` kayıtlı Roblox kontrolü
  yokken UNKNOWN değerlendirmesi üretir; `decide()` en fazla İZLE verir, **FIRSAT/AKIM/DOYMUŞ vermez**. `rising_unverified` bildirimi
  (momentum ≥ `scoring.unverified_alert_momentum`). Roblox gelince oyunlar `last_roblox_scan IS NULL` olduğundan otomatik taranır.
- **Başlangıç özeti (bootstrapping)**: DB boşken (`decisions_count()==0`) tekil bildirimler bastırılıp tek özet gider. YouTube için ayrıca
  `youtube_first_poll_done` anahtarı: ilk turda ayrı ayrı bildirim yerine tek "Fragman izleme başladı" özeti.
- **YouTube**: anahtarsız RSS; 13 varsayılan kanal `UC...` kimliğiyle (`config.DEFAULT_YOUTUBE_CHANNELS`). Başlık ayrıştırıcı bilerek
  muhafazakâr (gerçek 160 başlıkla ayarlı, `tests/fixtures/youtube_titles.json`). Oyun adı **Steam'de doğrulanır**: çıkmış / DLC-ek paket
  (ana oyun çıkmışsa) / büyük yayıncı → elenir; `is_coming_soon` → `upcoming`; mağazası yok → `unlisted` (eşik +0.15, yalnızca MEDIUM,
  büyük franchise listesi `BIG_FRANCHISES` ile elenir). Steam'e ulaşılamazsa **tahmin yürütülmez**, sonraki turda denenir.
  `buzz_score` logaritmik (izlenme + yaşa göre hız + ölçümler arası hız); `alert_buzz` 0.4, ≥0.65 HIGH. Roblox durumu yalnızca ek bilgi.
- **Güvenlik varsayılanları**: `web.host` varsayılan `127.0.0.1`; token yoksa yazma uçları yalnızca loopback'ten; Host doğrulaması
  (DNS rebinding, `web.allowed_hosts`); POST için `application/json` zorunlu; hız/eşzamanlılık/SSE sınırları; CSP. **ntfy konu adı bir
  paroladır** (bilen okur ve yazar): `http.register_secret()` ile log/hata/panelde maskelenir; zayıf konu adı uyarılır. Oyun adları dış
  kaynaklıdır: Slack/Discord/ntfy/e-posta enjeksiyonları `notify/` katmanında engellenir (`oneline`, `safe_url`, Slack kaçışı,
  `allowed_mentions`). Dashboard'un `onclick`/satır içi betikleri yüzünden CSP `script-src 'unsafe-inline'` içerir; XSS'e karşı asıl
  savunma `esc()`/`safeUrl()`.
- **Sistem bildirimleri**: ham istisna/dosya yönlendirmesi yok; "sebep + ne yapıyorum + ne yapmalısın" (`diagnostics.explain_error`).
- **Termux/Android**: sunucu telefonda önerilmez ama mümkün (Phantom Process Killer); C uzantılı bağımlılıklar `*_NO_EXTENSIONS=1` ile saf-Python kurulur.
- **Windows**: `.ps1` dosyaları **UTF-8 BOM** ister (PowerShell 5.1); `.cmd` dosyaları yalnızca ASCII + CRLF; kurallar `.gitattributes`'ta.
  `pythonw` (konsolsuz) çalışmada log varsayılan `data/akim.log`; `SetThreadExecutionState` ile uyku engeli; `signal.signal` yedeği.

## Doğrulanmış dış servis olguları (2026-10-06, canlı denendi)
- Roblox `apis.roblox.com/search-api/omni-search` anlamsız sorguya bile sonuç döner; `games.roblox.com/v1/games` geçersiz kimlikte 400 döner (→ sorgu-özgü, kesinti sayılmaz).
- Steam `IStoreBrowseService/GetItems` (`include_release`): `release.is_coming_soon`, `steam_release_date`. `storesearch` ile `find_by_title` (oran ≥ 0.9).
  `search/results/?filter=popularcomingsoon&json=1` "popüler yakında" listesi döndürür (**henüz kullanılmıyor**, bkz. fikirler).
- YouTube RSS `feeds/videos.xml?channel_id=UC...`: ~15 video, `media:statistics views` var. `@tanıtıcı` → kanal sayfasındaki `canonical` bağlantıdan çözülür.
- ntfy: `ntfy://<sunucu>/<konu>` derin bağlantısı aboneliği açar; sunucular mesajı varsayılan ~12 saat saklar (ntfy.sh için ayrıca doğrulanmadı); iOS uygulaması App Store'da.

## Çalıştırma ve test
```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
python -m pytest -q                      # ~353 test, ~3 sn (Python 3.10/3.12/3.13'te geçti; anthropic gerekmez)
akim -c config.yaml once --limit 12      # gerçek ağda uçtan uca kısa tur (Steam+Epic+YouTube+Roblox, ~45 sn)
akim report --days 7 | akim invite
bandit -q -r akim -ll -ii && pip-audit . # CI'daki güvenlik işi
```
- Test düzeni: `tests/test_engine.py` içinde `build()`, `FakeSteam/FakeEpic/FakeRoblox/Capture`; `test_resilience.py` içinde `FlakyRoblox`;
  `test_youtube.py` içinde sahte YouTube. `from tests.test_engine import build` kalıbı kullanılır. Gerçek alt süreç testleri `test_process.py`.
- Sabit test verileri **gerçek veriden**: `tests/fixtures/` (YouTube başlıkları/RSS, Steam arama HTML, benzerlik vakaları). Yenilemek istersen canlı çek, çıktıyı gözle doğrula.
- CI (`.github/workflows/ci.yml`): test (Ubuntu+Windows × 3.10/3.12), windows-scripts (PowerShell 5.1: kurulum, Görev Zamanlayıcı, run.cmd), termux-scripts (sh), security (bandit+pip-audit). Son durum: 7/7 yeşil (commit 12f84b7).
- Bandit: dinamik SQL satırları `# nosec B608` ile gerekçelendirildi (yalnızca sabit parça + `?`). `_BOARD_SQL` şablonu `.replace("{where}", ...)` ile doldurulur.
- Yerel UI doğrulaması için Playwright+Chromium kullanıldı (CSP altında, zararlı oyun adıyla): betikler depoda yok; gerekirse yeniden yaz.

## Doğrulanmadı (kendin dene / kullanıcıdan iste)
- Gerçek Android/HyperOS cihazda hiçbir şey denenmedi (Termux kurulumu yalnızca Linux'ta derleyicisiz simüle edildi). Mi Tarayıcı denenmedi.
- Gerçek ntfy/Telegram/Discord/Slack/e-posta **gönderimi** denenmedi (testler sahte sunucu kullanır).
- Docker imaj derlemesi, `docker-compose` ve systemd sıkılaştırma satırları denenmedi.
- Gerçek bir VPN kopması denenmedi (hızlandırılmış zamanla `_scanner_loop` testi var).
- 13 kanal dışındaki YouTube başlık biçimleri; Steam'de yanlış ad eşleşmesi nadir ama olabilir (güvenli taraf: eler).

## Bilinen sınırlar ve fikirler
- Oynanış benzerliği sözlük tabanlı; %60-75 bandı belirsiz. Laya ince ayar yolu hazır (`eval/laya_dataset_*.jsonl`) ama yapılmadı.
- Fikirler (istenmedi, yalnızca not): Steam `popularcomingsoon` listesini ikinci "yaklaşan" kaynağı yapmak; özetlere `report` bölümü;
  kanal başına öğrenilen taban izlenmeye göre ses getirme; Roblox kesintisinde geçmiş kontrollerin yaşını bildirimde göstermek.
- Davranış değişiklikleri (kullanıcıya hatırlat): panel varsayılan olarak yalnızca yerel; ağdan açmak için `AKIM_WEB_HOST=0.0.0.0` + `AKIM_WEB_TOKEN`.

## Tuzaklar
- `pkill -f <desen>` komutunu araç kabuğunun kendi komut satırında desen geçerken kullanma (kabuğu öldürür); PID ile öldür.
- Dosya kodlaması/satır sonu: `.ps1` BOM, `.cmd` ASCII+CRLF, diğerleri LF (`.gitattributes`). Betikleri Python ile düzenlerken BOM'u koru.
- `load_config`: göreli `data_dir`/`log_file` **config dosyasının klasörüne** göre çözülür; `.env` config'in yanından okunur ve gerçek ortam değişkenini ezmez.
- `Config` bilinmeyen anahtarda `ValueError` verir (yazım hatası yakalansın); kaldırılan bölümler `REMOVED_SECTIONS` ile uyarıyla atlanır.
- Dashboard tek dosya ve satır içi betik içerir; CSP'yi sıkılaştırmak için önce `onclick`/`onerror` öznitelikleri kaldırılmalı.
- `Storage` tek süreçli, eşzamanlı sqlite3; şema değişikliği için `SCHEMA` (IF NOT EXISTS) + `MIGRATIONS` listesi.

## Geçmiş (commit özeti)
`fdf2230` ilk sistem → `419735d` oynanış benzerliği → `2b8a92d` Laya (varsayılan kapalı, ölçüldü) → `dd8df72`/`ba2131b` Windows+Android
(HyperOS) → `0b88621` ücretli bileşen kaldırıldı + Termux → `030506e` Roblox/VPN dayanıklılığı → `b851ef3` YouTube fragmanları →
`4ab0a9d` güvenlik + report + invite → `d1246b3` belgeler → `12f84b7` ayar hataları, ilk fragman özeti, uçtan uca VPN testi.
