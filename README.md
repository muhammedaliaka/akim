# Akım — Steam & Epic → Roblox fırsat radarı

Akım, 7/24 açık kalan bir izleme servisidir:

1. **Steam** ve **Epic Games Store** listelerini (en çok satanlar, en çok oynananlar, trend, yeni çıkanlar…) sürekli çeker.
2. Büyük markaları (EA, Ubisoft, Sony, Tencent…) eler; **az bilinen / bağımsız yapımcıların** yükselen oyunlarını bulur.
3. Her adayı **Roblox'ta arar**. Klonları yalnızca isme göre değil **oynanışa göre** tespit eder. Örneğin adında "CS2" geçmeyen ama "5v5, bombayı kur/çöz" oynatan bir Roblox oyunu Counter-Strike klonu sayılır (bkz. [Benzerlik nasıl hesaplanır](#benzerlik-nasıl-hesaplanır)).
4. Sıra tırmanışı, yeni giriş, oyuncu artışı ve Roblox doygunluğunu birleştirip **karar verir**.
5. Durum değiştiği anda Telegram, telefon (ntfy), Discord, Slack, e-posta veya webhook ile **bildirim gönderir**.
6. Canlı bir **web paneli** sunar ve Telegram üzerinden komut alır (`/firsatlar`, `/kontrol <oyun>`).

```
Steam ─┐                                         ┌─ Telegram (+ komutlar)
       ├─► liste toplama ─► bağımsızlık filtresi ─► Roblox taraması ─► karar motoru ─┼─ ntfy (telefon push)
Epic ──┘        │                                      │   ▲                          ├─ Discord / Slack
                └── sıra geçmişi, anlık oyuncu ─────────┘   └── klon oyuncu takibi     ├─ E-posta / Webhook
                              (SQLite)                                                └─ Web paneli (canlı)
```

## Karar mantığı

Her oyun için üç bileşen hesaplanır (0–1):

| Bileşen | Neye bakar |
|---|---|
| **Bağımsızlık (B)** | Yayıncı/geliştirici büyük marka mı, orta ölçekli mi, bilinmeyen mi; Steam "Indie" etiketi; kendi yayınlama; erken erişim |
| **Momentum (M)** | Liste sırası, listelere yeni giriş, 24 saatlik sıra tırmanışı, Steam anlık oyuncu artışı, çıkış tarihinin yeniliği. Yıllardır listede duran oyunlar ve **indirim kaynaklı** tırmanışlar düşük puan alır |
| **Roblox doygunluğu (D)** | Eşleşen Roblox oyunu sayısı, toplam anlık oyuncu, en yüksek ziyaret |

```
fırsat skoru = 0.25·B + 0.45·M + 0.30·(1 − D)
```

| Karar | Koşul | Anlamı |
|---|---|---|
| 🟢 **FIRSAT** | Roblox'ta yok/erken, skor ≥ 0.65, momentum ≥ 0.3 | Oyun yükseliyor, Roblox'ta karşılığı yok. İlk hamle için doğru an |
| 🚀 **AKIM BAŞLADI** | Roblox benzerlerinin oyuncusu 24 saatte ≥1.8 kat arttı | Akım Roblox'a geçti; hızlı olmak gerekiyor |
| 🟡 **İZLE** | skor ≥ 0.5, momentum ≥ 0.2 | Sinyal var, takipte |
| 🔴 **DOYMUŞ** | Roblox tarafı dolu | Geç kalındı |
| ⚫ **ELENDİ** | bağımsızlık < 0.5 | Büyük marka, kapsam dışı |

Roblox durumları: **YOK** · **Erken aşama** (birkaç zayıf klon) · **Yükselişte** · **Rekabetçi** · **Doymuş**.

## Benzerlik nasıl hesaplanır

Sistem Roblox oyununda mağaza oyununun adının geçmesini beklemez. Her aday için dört bağımsız kanıt toplar ve bunları tek bir **% benzerlik** oranına indirger:

| Kanıt | Neye bakar | Örnek |
|---|---|---|
| **İsim** | Emoji, `[UPDATE]` gibi süslerden arındırılmış isim benzerliği | "Schedule I" ~ "Schedule X" |
| **Atıf** | Açıklamada orijinal oyuna gönderme (aynı cümle içinde) | "inspired by the viral title PEAK" |
| **Oynanış** | Steam etiketleri ve açıklamadan çıkarılan oynanış kavramlarının Roblox açıklamasıyla örtüşmesi | Counter-Strike 2 profili: *taktik FPS (bomba kur/çöz)*. Roblox'ta "5v5, plant the bomb, buy weapons" → %84 |
| **Claude** (isteğe bağlı) | Oynanış döngüsünü bütün bağlamıyla karşılaştıran LLM değerlendirmesi | "aynı çekirdek döngü, farklı tema: %78 esinlenme" |

- **Oynanış kavramları.** 40'tan fazla oynanış kavramı eş anlamlı terimleri bir araya toplar: *tırmanış*, *kota/ganimet toplama*, *sosyal çıkarım (hain kim)*, *market işletme*, *hayalet avı*… Açıklamanın ilk cümlesindeki kavramlar ana oynanış sayılır; etiketler ikincildir. "FPS", "korku" gibi geniş türler, "bomba kur/çöz" gibi ayırt edici mekaniklerden daha az kanıt değeri taşır.
- **Birleştirme.** Kanıtlar olasılıksal olarak birleştirilir (noisy-OR). Tek başına ikna etmeyen iki kanıt birlikte güçlü olabilir: Counter Blox'ta isim %57 + oynanış %84 → **%90 klon**.
- **Yanlış pozitif korumaları:**
  - Aynı isim ama bambaşka oynanış klon sayılmaz.
  - Açıklaması zayıf tek geniş kavramlı profiller "tam örtüşme" iddia edemez.
  - Mağaza adındaki kelimeler oynanış hesabına ikinci kez girmez.
- **Sınıflar.** Adaylar üç gruba ayrılır:
  - 🎯 **Klon:** aynı oyunu oynatıyor; Roblox doygunluğuna tam ağırlıkla katılır.
  - ≈ **Benzer oynanış:** aynı alt tür; sınırlı ek rekabet katkısı yapar.
  - **İlgisiz.**

  RIVALS gibi dev ama yalnızca aynı türdeki bir oyun, bir taktik nişancının Roblox'ta "zaten var" sayılmasına tek başına yetmez. "AKIM BAŞLADI" tespiti de yalnızca klonların oyuncu artışından yapılır.

Gerçek veriyle örnek (`akim check "Counter-Strike 2"`):

```
Counter-Strike 2 → Doymuş (4 klon, 7 benzer oynanış)
  % 99 KLON   [🧤] Defusal       açıklamada 'inspired by … counter strike' · oynanış %89 (taktik FPS)
  % 93 KLON   Defuse Division    açıklamada oyunun adı geçiyor · oynanış %73
  % 90 KLON   Counter Blox       isim %57 · oynanış %84 (taktik FPS (bomba kur/çöz))
  % 80 KLON   BloxStrike         oynanış %84 (taktik FPS (bomba kur/çöz))      ← isim hiç benzemiyor
  % 62 benzer RIVALS             oynanış %66 (FPS/nişancı)                      ← aynı tür, klon değil
```

### İsteğe bağlı Claude doğrulaması

Sezgisel skorlar %60-75 bandında "klon mu, aynı tür mü?" sorusunda belirsiz kalabilir. `llm.enabled: true` ve `ANTHROPIC_API_KEY` ile en iyi adaylar (oyun başına en fazla 10) Claude'a gönderilir. Claude her aday için 0-100 benzerlik, ilişki türü (*klon / esinlenme / aynı tür / ilgisiz*) ve kısa Türkçe gerekçe döner; bu kararın son sözü olur.

- **Model:** varsayılan `claude-opus-5-5`, `effort: low` (sınıflandırma işi). `llm.model` ile değiştirilebilir.
- **Maliyet kontrolü:**
  - Kararlar veritabanında önbelleklenir; aynı aday ve aynı açıklama 14 gün boyunca tekrar sorulmaz.
  - Saatlik çağrı sınırı vardır (varsayılan 30).
  - Ön puanı %30'un altındaki adaylar hiç gönderilmez.
  - Kaba tahmin: yeni bir oyun için tek çağrı ≈ 2 bin girdi + 1 bin çıktı token'ı, yani Opus 5.5 fiyatıyla yaklaşık $0,03. İlk açılışta ~90 oyun ≈ $3; sonrasında yalnızca yeni oyunlar ve yeni adaylar ücretlendirilir. (Bu tahmin ölçülmedi; gerçek tüketimi Anthropic konsolundan takip et.)
- **Güvenlik ağı:** API hatası, hız sınırı veya reddetme durumunda sistem sezgisel skorlarla devam eder. Reddetmede sunucu tarafı yedek model (`fallbacks: "default"`) devreye girer.

### İsteğe bağlı Laya (yerel, ücretsiz)

[Laya](https://github.com/NandhaKishorM/laya) (Convai Innovations, Apache 2.0) metin üretmeyen bir karar modelidir. Oyun çiftine "klon / esinlenme / aynı tür / ilgisiz" sorusunu sorar ve her seçenek için olasılık döner. Yerelde çalışır; API anahtarı ve ücret gerekmez.

Entegrasyonu gerçek veriyle ölçtüm. Tam tablo ve yöntem [eval/README.md](eval/README.md) içinde. Hiç görülmemiş 8 oyunda (112 etiketli çift) sonuçlar:

| | AUROC | Klon yakalama (duyarlılık) | Doğru "klon" oranı (kesinlik) |
|---|---|---|---|
| Akım sezgisel | 0.898 | 0.35 | 0.70 |
| Laya tek başına (en iyi soru biçimi) | 0.667 | – | – |
| **Akım + Laya (evidence modu)** | 0.897 | **0.50** | 0.53 |

- **Hazır Laya bu görevde eğitimsiz kullanımda zayıf.** Beş farklı soru biçimi ve iki checkpoint denendi. Laya'nın kendi dokümantasyonu da ince ayar öneriyor.
- **Evidence modu gerçek bir takas sunuyor.** Laya düşük güvenle diğer kanıtlara eklenir ve sezgisel motorun kaçırdığı yeni mekanikli klonların bir kısmını yakalar (duyarlılık 0.35 → 0.50). Karşılığında yanlış alarm artar.
- **Varsayılan kapalı.** Kaçırılan klon (Roblox'ta olan oyuna "FIRSAT" denmesi) senin için daha pahalıysa aç.
- **İnce ayar yolu.** Etiketli çiftler Laya'nın eğitim/ölçüm biçiminde hazır: `eval/laya_dataset_*.jsonl`. İnce ayarlanmış bir model `laya.model_path` + `mode: judge` ile doğrudan kullanılabilir.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU için
pip install "akim[laya]"
AKIM_LAYA_ENABLED=true akim check "Buckshot Roulette"
```

Docker'da `docker-compose.yml` içindeki `EXTRAS: "llm,laya"` ile derle. Model (~800 MB) ilk kullanımda `data/hf` altına iner. CPU'da oyun başına ~10-20 sn sürer; sonuçlar önbelleklenir.

### Bildirim türleri

| Olay | Öncelik | Ne zaman |
|---|---|---|
| `opportunity` | Yüksek / **Kritik** | Oyun FIRSAT durumuna geçti (Roblox'ta hiç yok ve momentum yüksekse kritik) |
| `rising_trend` | Yüksek | Roblox'taki klonlar hızla büyüyor |
| `first_clone` | Yüksek | Daha önce karşılığı olmayan oyunun **ilk Roblox klonu** çıktı |
| `rank_surge` / `ccu_surge` / `chart_entry` | Orta–Yüksek | Takipteki oyun sıçradı / listeye girdi |
| `saturated` | Bilgi | Fırsat penceresi kapandı |
| `digest` | Orta | Periyodik özet (varsayılan 6 saatte bir) |
| `system` | Yüksek | Bir veri kaynağına 3 kez üst üste ulaşılamadı / düzeldi |

Aynı oyun + aynı olay için 12 saat tekrar koruması vardır. Aynı oyun hem Steam'de hem Epic'te listelenmişse **tek bildirim** gider. İlk açılışta, mevcut her fırsat için ayrı bildirim göndermek yerine **tek bir başlangıç özeti** gönderilir.

## Platformlar

| Platform | Rol | Nasıl |
|---|---|---|
| **Windows 10/11** | Sunucu (PC açık kaldığı sürece) | [docs/windows.md](docs/windows.md): `deploy\windows\install.cmd` → `run.cmd` veya `autostart.ps1` |
| **Linux / VPS** | Sunucu, gerçek 7/24 | Docker (aşağıda) veya `deploy/akim.service` (systemd) |
| **Android (HyperOS / Xiaomi dahil)** | İstemci: anlık bildirim + panel | [docs/android-hyperos.md](docs/android-hyperos.md): ntfy/Telegram + HyperOS arka plan ayarları + panel kısayolu |

Sunucuyu **telefonda çalıştırmak önerilmez** (Android arka plan süreçlerini öldürür); telefon bildirimi alır ve paneli açar.
Windows ve Android tarafı kodda şunları kapsar: UTF-8 konsol, `.env` okuma, Ctrl+C/Ctrl+Break ile temiz kapanış, boşta uykuyu engelleme, konsolsuz çalışmada dosya logu, port doluysa panelsiz devam, yüklenebilir (PWA) mobil panel.
Testler CI'da Ubuntu ve Windows üzerinde, Python 3.10 ve 3.12 ile çalışır.

## Hızlı başlangıç

### Docker (önerilen, 7/24)

```bash
cp config.example.yaml config.yaml     # gerekirse düzenle
cp .env.example .env                   # bildirim anahtarlarını gir
docker compose up -d --build
docker compose logs -f                 # canlı log
```

Panel: <http://localhost:8080>. `restart: unless-stopped` sayesinde servis çökerse veya sunucu yeniden başlarsa kendiliğinden ayağa kalkar. Veriler `./data` altında kalıcıdır.

### Doğrudan Python (3.10+)

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: deploy\windows\install.cmd
pip install -e .
cp config.example.yaml config.yaml
cp .env.example .env                             # config.yaml'ın yanındaki .env otomatik okunur
akim run                                         # veya: python -m akim run
```

`.env` dosyası config dosyasının klasöründen (yoksa çalışma klasöründen) okunur; gerçek ortam değişkenleri `.env`'in önüne geçer.
Göreli `data_dir` ve `log_file` yolları çalışma klasörüne değil **config dosyasının klasörüne** göre çözülür (Görev Zamanlayıcı/systemd çalışma klasörünü kendi seçer).
Sunucuda systemd servisi olarak çalıştırmak için `deploy/akim.service` dosyasındaki adımlara bak.

## Bildirim kurulumu

En hızlı yol, `.env` dosyasına bir değişken eklemek. Config'e dokunmadan kanal açılır.

| Kanal | Kurulum |
|---|---|
| **Telegram** | [@BotFather](https://t.me/BotFather) ile bot oluştur, sohbet kimliğini [@userinfobot](https://t.me/userinfobot) ile öğren → `AKIM_TELEGRAM_BOT_TOKEN`, `AKIM_TELEGRAM_CHAT_ID` |
| **Telefon push (ntfy)** | ntfy uygulamasını kur, tahmin edilemez bir konuya abone ol (ör. `akim-4f9c2a7e`) → `AKIM_NTFY_TOPIC`. Varsayılan olarak yalnızca yüksek/kritik bildirimler gelir |
| **Discord** | Kanal ayarları → Entegrasyonlar → Webhook → `AKIM_DISCORD_WEBHOOK_URL` |
| **Slack** | Incoming Webhook → `AKIM_SLACK_WEBHOOK_URL` |
| **E-posta / Webhook** | `config.yaml` → `notifications.channels` |

Her kanalın kendi `min_priority` eşiği vardır (`low`, `medium`, `high`, `critical`). Örneğin telefona yalnızca kritik fırsatlar, Discord'a her şey gidebilir. Kurulumu test etmek için:

```bash
akim test-notify
```

### Telegram komutları

| Komut | Açıklama |
|---|---|
| `/firsatlar` | Güncel fırsat, akım ve izleme listesi |
| `/kontrol <oyun>` | Herhangi bir oyunu **anında** Roblox'ta kontrol et |
| `/oyun <oyun>` | Takip edilen oyunun detayı: sıralar, gerekçe, Roblox benzerleri |
| `/durum` | Sistem ve kaynak sağlığı |

Komutlar yalnızca yapılandırılmış `chat_id`'den kabul edilir.

## Komut satırı

```bash
akim run                    # 7/24 izleme + web paneli
akim once --limit 30        # tek tur: listeleri çek, 30 adayı Roblox'ta tara, tabloyu yazdır
akim check "Schedule I"     # bir oyunu anında Roblox'ta kontrol et
akim top                    # veritabanındaki güncel tablo
akim test-notify            # tüm kanallara test bildirimi
```

Örnek `akim check "R.E.P.O."` çıktısı:

```
R.E.P.O. → Rekabetçi (doygunluk 0.53, toplam 103 anlık oyuncu)
  1.00  REPO Game      10 oyuncu   6.7M ziyaret  (isim %100 + açıklamada 'inspired by … repo' ifadesi)
  0.95  SEIZE 😂        73 oyuncu  87.6M ziyaret  (açıklamada 'inspired by … repo' ifadesi)
  0.95  REPO SANDBOX   20 oyuncu  10.2M ziyaret  (açıklamada 'inspired by … repo' ifadesi)
```

## Web paneli ve API

- `GET /` : canlı panel (Server-Sent Events ile anlık güncellenir, tarayıcı bildirimi destekler)
- `GET /api/board?decision=opportunity,watch` : karar tablosu
- `GET /api/games/{key}` : oyun detayı (sıra geçmişi, anlık oyuncu, Roblox benzerleri)
- `GET /api/alerts` : son bildirimler
- `GET /api/events` : canlı olay akışı (SSE)
- `POST /api/check` `{"title": "..."}` : anlık Roblox kontrolü
- `POST /api/rescan/{key}` : bir oyunu hemen yeniden tara
- `GET /healthz` : sağlık kontrolü (Docker healthcheck bunu kullanır)

`AKIM_WEB_TOKEN` tanımlıysa POST uç noktaları `Authorization: Bearer <token>` ister. Paneli internete açacaksan önüne kimlik doğrulamalı bir ters vekil (reverse proxy) koy.

## Döngüler ve varsayılan sıklıklar

| Döngü | Sıklık | Görev |
|---|---|---|
| Steam listeleri | 15 dk | Anlık/haftalık en çok satanlar, anlık en çok oynananlar (top 100) |
| Epic listeleri | 30 dk | Top Sellers, Most Played, Trending, Most Popular, Top New Releases, Top Wishlisted, Top Player Rated |
| Roblox taraması | sürekli | Yeni/sinyalli oyunlar önce; normal oyunlar 6 saatte, FIRSAT oyunları 1,5 saatte bir |
| Klon takibi | 15 dk | Bilinen Roblox benzerlerinin anlık oyuncusu (akım Roblox'a geçti mi?) |
| Steam anlık oyuncu | 30 dk | Takipteki bağımsız oyunların oyuncu sayısı (ani sıçrama tespiti) |
| Özet | 6 saat | En iyi 10 fırsat |

Tüm API çağrılarında yeniden deneme, üstel geri çekilme ve host bazlı hız sınırı vardır. 429 alan hostun bekleme süresi otomatik büyür, başarılı çağrılarla yeniden küçülür. Log'larda bot token ve webhook adresleri maskelenir.

## Sınırlamalar

- Oynanış benzerliği kavram sözlüğüne ve açıklamalara dayanır. Açıklaması çok kısa veya boş Roblox oyunları ve sözlükte karşılığı olmayan yeni türler için isabet düşer. %60-75 bandındaki "klon mu, aynı tür mü?" ayrımı sezgiseldir; bu bant için isteğe bağlı Claude doğrulaması önerilir. Eşleşme sayılmayan en yakın adaylar panelde yüzdeleriyle ayrıca listelenir.
- Roblox aramasının döndürmediği oyunlar hiç değerlendirilmez. Sistem, Roblox'un kendi aramasında oyun adıyla bulunan adaylarla sınırlıdır.
- "Peak", "Halloween" gibi **genel isimler** için isim eşleşmesi yalnızca neredeyse birebir ise kabul edilir ve kararın yanında "elle doğrula" notu çıkar.
- Epic, tam koleksiyon sayfalarını Cloudflare arkasında tuttuğu için her Epic listesinden mağaza ana sayfasında görünen ilk ~15 oyun alınır.
- Kullanılan uç noktalar herkese açık ama resmi olarak belgelenmemiştir; biçimleri değişebilir. Bir kaynak bozulursa sistem çalışmaya devam eder ve `system` bildirimi gönderir.

## Geliştirme

```bash
pip install -e ".[dev]"
pytest
```

Proje yapısı:

```
akim/
  sources/    steam.py · epic.py · roblox.py     veri toplayıcılar
  analysis/   indie.py      büyük marka / bağımsız ayrımı
              matching.py   isim benzerliği, açıklamada atıf
              concepts.py   oynanış kavramları ve konsept benzerliği
              similarity.py kanıtların birleştirilmesi, klon / benzer sınıflandırması
              judge.py      isteğe bağlı Claude doğrulaması
              laya_judge.py isteğe bağlı yerel Laya karar modeli
              scoring.py    momentum, Roblox doygunluğu, karar
  notify/     telegram.py · channels.py           bildirim kanalları + dağıtıcı
  web/        server.py · dashboard.html          canlı panel ve API
              static/                             PWA: manifest, simgeler, servis çalışanı
  runtime.py  Windows konsolu, uyku engelleme, .env, kapatma sinyalleri, yerel ağ adresi
  engine.py   döngüler, karar geçişleri, bildirim üretimi
  storage.py  SQLite (sıra geçmişi, Roblox geçmişi, kararlar, bildirim tekrar koruması)
deploy/       akim.service (systemd) · windows/ (kurulum, çalıştırma, otomatik başlatma)
docs/         windows.md · android-hyperos.md
eval/         etiketli gerçek veri, değerlendirme betiği ve sonuçlar (bkz. eval/README.md)
```
