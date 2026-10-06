# Akım — Steam & Epic → Roblox fırsat radarı

Akım, 7/24 açık kalan bir izleme servisidir:

1. **Steam** ve **Epic Games Store** listelerini (en çok satanlar, en çok oynananlar, trend, yeni çıkanlar…) sürekli çeker.
2. Büyük markaları (EA, Ubisoft, Sony, Tencent…) eler; **az bilinen / bağımsız yapımcıların** yükselen oyunlarını bulur.
3. Her adayı **Roblox'ta arar**. Klonları yalnızca isme göre değil **oynanışa göre** tespit eder. Örneğin adında "CS2" geçmeyen ama "5v5, bombayı kur/çöz" oynatan bir Roblox oyunu Counter-Strike klonu sayılır (bkz. [Benzerlik nasıl hesaplanır](#benzerlik-nasıl-hesaplanır)).
4. **Roblox'a ulaşılamazsa** (ör. VPN kapalı) durmaz: Roblox bilgisi olmadan "yükseliyor" raporu verir, bağlantı gelince otomatik doğrular ([ayrıntı](#roblox-erişilemezken-vpn)).
5. **YouTube fragmanlarını** izleyip henüz **çıkmamış** oyunları da yakalar; Roblox'ta karşılığı olması şart değildir ([ayrıntı](#çıkmamış-oyunlar-youtube-fragmanları)).
6. Sıra tırmanışı, yeni giriş, oyuncu artışı ve Roblox doygunluğunu birleştirip **karar verir**.
7. Durum değiştiği anda Telegram, telefon (ntfy), Discord, Slack, e-posta veya webhook ile **bildirim gönderir**.
8. Canlı bir **web paneli** sunar ve Telegram üzerinden komut alır (`/firsatlar`, `/kontrol <oyun>`, `/yaklasan`, `/rapor`).

```
Steam ─┐                                         ┌─ Telegram (+ komutlar)
       ├─► liste toplama ─► bağımsızlık filtresi ─► Roblox taraması ─► karar motoru ─┼─ ntfy (telefon push)
Epic ──┘        │                                      │   ▲                          ├─ Discord / Slack
                └── sıra geçmişi, anlık oyuncu ─────────┘   └── klon oyuncu takibi     ├─ E-posta / Webhook
                              (SQLite)                                                └─ Web paneli (canlı)
```

## Ücretsiz çalışır

Akım'da para, kredi veya ücretli API anahtarı isteyen hiçbir bileşen yoktur:

- **Veri:** Steam, Epic ve Roblox'un herkese açık uç noktaları; hesap veya anahtar gerekmez.
- **Analiz:** isim, atıf ve oynanış analizi yerelde çalışır. İsteğe bağlı Laya modeli de yerel ve Apache 2.0 lisanslıdır.
- **Bildirim:** Telegram botu, ntfy, Discord/Slack webhook'u, e-posta (ücretsiz bir SMTP hesabıyla) ve genel webhook.
- Ücretli bir LLM (Claude/API) doğrulaması bir ara vardı; **kaldırıldı**. Eski bir `config.yaml` içinde `llm:` bölümü kalmışsa uyarıyla yok sayılır.

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

Sistem Roblox oyununda mağaza oyununun adının geçmesini beklemez. Her aday için üç bağımsız kanıt (isteğe bağlı olarak dördüncüsü: yerel Laya) toplar ve bunları tek bir **% benzerlik** oranına indirger:

| Kanıt | Neye bakar | Örnek |
|---|---|---|
| **İsim** | Emoji, `[UPDATE]` gibi süslerden arındırılmış isim benzerliği | "Schedule I" ~ "Schedule X" |
| **Atıf** | Açıklamada orijinal oyuna gönderme (aynı cümle içinde) | "inspired by the viral title PEAK" |
| **Oynanış** | Steam etiketleri ve açıklamadan çıkarılan oynanış kavramlarının Roblox açıklamasıyla örtüşmesi | Counter-Strike 2 profili: *taktik FPS (bomba kur/çöz)*. Roblox'ta "5v5, plant the bomb, buy weapons" → %84 |
| **Laya** (isteğe bağlı, yerel ve ücretsiz) | Karar modelinin "klon / esinlenme / aynı tür / ilgisiz" yorumu; düşük güvenle diğer kanıtlara eklenir | aşağıdaki Laya bölümüne bak |

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

Docker'da `docker-compose.yml` içindeki `EXTRAS: "laya"` ile derle. Model (~800 MB) ilk kullanımda `data/hf` altına iner. CPU'da oyun başına ~10-20 sn sürer; sonuçlar önbelleklenir.

### Bildirim türleri

| Olay | Öncelik | Ne zaman |
|---|---|---|
| `opportunity` | Yüksek / **Kritik** | Oyun FIRSAT durumuna geçti (Roblox'ta hiç yok ve momentum yüksekse kritik) |
| `rising_trend` | Yüksek | Roblox'taki klonlar hızla büyüyor |
| `first_clone` | Yüksek | Daha önce karşılığı olmayan oyunun **ilk Roblox klonu** çıktı |
| `rising_unverified` | Orta–Yüksek | Bağımsız oyun yükseliyor ama **Roblox doğrulanamıyor** (VPN kapalı/Roblox kapalı); gelince otomatik doğrulanır |
| `upcoming_trailer` | Orta–Yüksek | YouTube'da fragmanı ses getiren, **henüz çıkmamış** oyun (Steam'de "Yakında" ya da mağazasız) |
| `rank_surge` / `ccu_surge` / `chart_entry` | Orta–Yüksek | Takipteki oyun sıçradı / listeye girdi |
| `saturated` | Bilgi | Fırsat penceresi kapandı |
| `digest` | Orta | Periyodik özet (varsayılan 6 saatte bir) |
| `system` | Yüksek / Bilgi | Bir kaynağa ulaşılamıyor (sade Türkçe sebep + ne yapman gerektiği) / yeniden çalışıyor. Roblox kesintisi 10 dakikayı geçerse **tek** uyarı gider |

Aynı oyun + aynı olay için 12 saat tekrar koruması vardır. Aynı oyun hem Steam'de hem Epic'te listelenmişse **tek bildirim** gider. İlk açılışta, mevcut her fırsat için ayrı bildirim göndermek yerine **tek bir başlangıç özeti** gönderilir.

## Roblox erişilemezken (VPN)

Roblox bazı ağlarda engellidir ve VPN gerektirebilir. VPN kapandığında:

- Sistem **"Roblox'ta yok" demez**. Ulaşılamayan, bozuk ya da boş yanıt "bilmiyorum" sayılır; aksi hâlde sahte FIRSAT çıkardı. Sağlıklı Roblox hiçbir aramaya boş dönmediğinden boş yanıt da engel işareti kabul edilir.
- İki ardışık hatadan sonra **Roblox'suz mod** başlar: oyunlar yalnızca bağımsızlık ve momentumla değerlendirilir, en fazla İZLE olur ve bildirim "Roblox doğrulanamadı" notu taşır (`rising_unverified`).
- Roblox her 5 dakikada bir yoklanır. Kesinti 10 dakikayı aşarsa tek bir uyarı gelir ("VPN'i açman yeterli"); bağlantı gelince doğrulanamayan oyunlar otomatik taranır, gerçek karar ve FIRSAT bildirimi o zaman verilir.
- Kesintiler kaydedilir: `akim report`. Roblox'u hiç kullanmak istemezsen `roblox.enabled: false` (sistem hep Roblox'suz çalışır).

## Çıkmamış oyunlar: YouTube fragmanları

Roblox'ta karşılığı aramadan, oyunlar **çıkmadan** fragmanından öğrenilir. Hesap, API anahtarı ve ücret gerekmez (kanal RSS akışları).

1. 13 kanalın (Steam, IGN, GameSpot, GameTrailers, PC Gamer, Game Informer, GamesRadar, Day of the Devs, Indie Game Trailers, IndieGame+, Devolver, Annapurna, Hooded Horse) son videoları saatte bir okunur. Kanalları `youtube.channels` ile değiştirebilirsin (`UC...` kimliği ya da `@tanıtıcı`).
2. Başlıktan oyun adı çıkarılır ("X - Official Reveal Trailer", "X | Release Date Trailer"). İnceleme, liste, etkinlik, müzik, DLC ve güncelleme videoları elenir.
3. Oyun **Steam'de aranır**: çıkmışsa ya da ek paketse elenir, büyük yayıncıysa elenir; "Yakında" sayfası varsa **çıkmamış bağımsız oyun** sayılır. Steam'e ulaşılamazsa tahmin yürütülmez, sonraki tur denenir.
4. Mağaza sayfası olmayanlar (konsol/Epic/yeni duyuru) daha yüksek eşikle ve yalnızca orta öncelikle bildirilir; büyük markalar elenir.
5. **Ses getirme** puanı (0–1): toplam izlenme, yaşa göre hız ve ölçümler arası hız. Varsayılan eşik 0,4; 0,65 üstü yüksek öncelik.
6. Bildirim çıkış tarihini, fragmanı, yapımcıyı ve Roblox durumunu (erişilebiliyorsa, ek bilgi olarak) taşır. Aynı oyun için bir kez bildirilir.

Panelde **Yaklaşanlar** sekmesi, Telegram'da `/yaklasan` ve özetlerde "Yaklaşan oyunlar" bölümü bulunur.

## Platformlar

| Platform | Rol | Nasıl |
|---|---|---|
| **Windows 10/11** | Sunucu (PC açık kaldığı sürece) | [docs/windows.md](docs/windows.md): `deploy\windows\install.cmd` → `run.cmd` veya `autostart.ps1` |
| **Linux** (kendi makinen, Raspberry Pi, eski bilgisayar) | Sunucu, sürekli açık kaldığı sürece | Docker (aşağıda) veya `deploy/akim.service` (systemd) |
| **Android (HyperOS / Xiaomi dahil)** | İstemci: anlık bildirim + panel | [docs/android-hyperos.md](docs/android-hyperos.md): ntfy/Telegram + HyperOS arka plan ayarları + panel kısayolu |
| **Android + Termux** | Sunucu (telefonun kendisinde) | [docs/termux.md](docs/termux.md): `sh deploy/termux/install.sh` → `sh deploy/termux/run.sh` |

Sunucuyu telefonda çalıştırmak mümkündür ([docs/termux.md](docs/termux.md): Termux, `deploy/termux/install.sh`) ama riskli: Android arka plan süreçlerini öldürebilir. Güvenilir düzen, sunucunun PC/Linux'ta, telefonun istemci olmasıdır.
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
| `/yaklasan` | Fragmandan yakalanan, henüz çıkmamış oyunlar |
| `/rapor [gün]` | Son günlerin sade raporu (bildirimler, kesintiler, dikkat edilecekler) |
| `/durum` | Sistem ve kaynak sağlığı (Roblox kesintisi dahil) |

Komutlar yalnızca yapılandırılmış `chat_id`'den kabul edilir.

## Komut satırı

```bash
akim run                    # 7/24 izleme + web paneli
akim once --limit 30        # tek tur: listeleri çek, 30 adayı Roblox'ta tara, tabloyu yazdır
akim check "Schedule I"     # bir oyunu anında Roblox'ta kontrol et
akim top                    # veritabanındaki güncel tablo
akim test-notify            # tüm kanallara test bildirimi
akim report --days 7        # son günlerin sade raporu: bildirimler, kesintiler, dikkat edilecekler
akim invite                 # arkadaşlar için ntfy katılım metni (WhatsApp'a yapıştır)
```

Arkadaşlarını bildirimlere katmak için: [docs/arkadaslar.md](docs/arkadaslar.md).

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
- `GET /api/upcoming` : fragmandan yakalanan çıkmamış oyunlar
- `GET /api/alerts` : son bildirimler
- `GET /api/events` : canlı olay akışı (SSE)
- `POST /api/check` `{"title": "..."}` : anlık Roblox kontrolü
- `POST /api/rescan/{key}` : bir oyunu hemen yeniden tara
- `GET /healthz` : sağlık kontrolü (Docker healthcheck bunu kullanır)

### Güvenlik varsayılanları

- Panel varsayılan olarak **yalnızca bu cihazdan** erişilebilir (`web.host: 127.0.0.1`). Telefondan/aynı ağdan açmak için `web.host: 0.0.0.0` ve `.env` içinde güçlü bir `AKIM_WEB_TOKEN` ayarla.
- Token yoksa kontrol uçları (`/api/check`, `/api/rescan`) yalnızca bu cihazdan çalışır; ağdan okuma açık olsa bile yazma 403 döner. Token varsa `Authorization: Bearer <token>` zorunludur.
- Host başlığı doğrulanır (DNS rebinding); IP ve `*.local` dışındaki adlar için `web.allowed_hosts` (ör. `*.ts.net`). POST için `application/json` zorunludur; yazma uçları hız sınırlıdır; CSP ve diğer güvenlik başlıkları her yanıtta bulunur.
- Bot token, webhook adresleri, parolalar ve **ntfy konu adı** log'larda, hata mesajlarında ve panelde maskelenir. Konu adı bir parola gibidir: bilen herkes bildirimlerini okuyabilir ve konuya mesaj gönderebilir; bu yüzden zayıf/kısa konu adları uyarılır.
- Oyun adları dış kaynaklıdır: Slack/Discord etiketleri, başlık ve e-posta enjeksiyonları kanal düzeyinde engellenir.
- Paneli internete açacaksan önüne kimlik doğrulamalı bir ters vekil (reverse proxy) koy; ya da VPN (ör. Tailscale) kullan.

## Döngüler ve varsayılan sıklıklar

| Döngü | Sıklık | Görev |
|---|---|---|
| Steam listeleri | 15 dk | Anlık/haftalık en çok satanlar, anlık en çok oynananlar (top 100) |
| Epic listeleri | 30 dk | Top Sellers, Most Played, Trending, Most Popular, Top New Releases, Top Wishlisted, Top Player Rated |
| Roblox taraması | sürekli | Yeni/sinyalli oyunlar önce; normal oyunlar 6 saatte, FIRSAT oyunları 1,5 saatte bir |
| Klon takibi | 15 dk | Bilinen Roblox benzerlerinin anlık oyuncusu (akım Roblox'a geçti mi?) |
| Steam anlık oyuncu | 30 dk | Takipteki bağımsız oyunların oyuncu sayısı (ani sıçrama tespiti) |
| YouTube fragmanları | 60 dk | Kanal akışları → çıkmamış oyun adayları (Steam doğrulaması) |
| Özet | 6 saat | En iyi 10 fırsat + yaklaşan oyunlar |

Tüm API çağrılarında yeniden deneme, üstel geri çekilme ve host bazlı hız sınırı vardır. 429 alan hostun bekleme süresi otomatik büyür, başarılı çağrılarla yeniden küçülür. Log'larda bot token ve webhook adresleri maskelenir.

## Sınırlamalar

- Oynanış benzerliği kavram sözlüğüne ve açıklamalara dayanır. Açıklaması çok kısa veya boş Roblox oyunları ve sözlükte karşılığı olmayan yeni türler için isabet düşer. %60-75 bandındaki "klon mu, aynı tür mü?" ayrımı sezgiseldir; bu bant için isteğe bağlı yerel Laya (evidence modu) eklenebilir ama ölçümlerde kazancı sınırlı kaldı (bkz. Laya bölümü). Eşleşme sayılmayan en yakın adaylar panelde yüzdeleriyle ayrıca listelenir.
- Roblox aramasının döndürmediği oyunlar hiç değerlendirilmez. Sistem, Roblox'un kendi aramasında oyun adıyla bulunan adaylarla sınırlıdır.
- "Peak", "Halloween" gibi **genel isimler** için isim eşleşmesi yalnızca neredeyse birebir ise kabul edilir ve kararın yanında "elle doğrula" notu çıkar.
- Epic, tam koleksiyon sayfalarını Cloudflare arkasında tuttuğu için her Epic listesinden mağaza ana sayfasında görünen ilk ~15 oyun alınır.
- Fragman tespiti başlık biçimine dayanır (her kanalın son ~15 videosu). Alışılmadık başlıklar kaçabilir; çıkmış bir oyunun adıyla Steam'de yanlış eşleşme olursa o oyun elenir (güvenli taraf). Mağazası olmayan oyunlarda bağımsızlık doğrulanamaz; bu yüzden eşik yüksek, öncelik orta tutulur.
- Roblox'suz modda (erişim yok) kararlar yalnızca bağımsızlık ve momentuma dayanır; "Roblox'ta yok" iddiası yapılmaz.
- Kullanılan uç noktalar herkese açık ama resmi olarak belgelenmemiştir; biçimleri değişebilir. Bir kaynak bozulursa sistem çalışmaya devam eder ve `system` bildirimi gönderir.

## Geliştirme

Projeye yeni başlayan geliştirici ya da yapay zeka oturumu için mimari, tasarım gerekçeleri, doğrulanmış dış servis olguları ve açık işler: [CLAUDE.md](CLAUDE.md).

```bash
pip install -e ".[dev]"
pytest
```

Proje yapısı:

```
akim/
  sources/    steam.py · epic.py · roblox.py · youtube.py   veri toplayıcılar
  analysis/   indie.py      büyük marka / bağımsız ayrımı
              matching.py   isim benzerliği, açıklamada atıf
              concepts.py   oynanış kavramları ve konsept benzerliği
              similarity.py kanıtların birleştirilmesi, klon / benzer sınıflandırması
              laya_judge.py isteğe bağlı yerel Laya karar modeli
              scoring.py    momentum, Roblox doygunluğu, karar
              trailers.py   YouTube başlığından oyun adı, ses getirme puanı
  notify/     telegram.py · channels.py           bildirim kanalları + dağıtıcı
  web/        server.py · dashboard.html          canlı panel ve API
              static/                             PWA: manifest, simgeler, servis çalışanı
  diagnostics.py  ham hata -> sade Türkçe neden; report.py  `akim report`; invite.py  ntfy davet metni
  runtime.py  Windows konsolu, uyku engelleme, .env, kapatma sinyalleri, yerel ağ adresi
  engine.py   döngüler, karar geçişleri, bildirim üretimi
  storage.py  SQLite (sıra geçmişi, Roblox geçmişi, kararlar, bildirim tekrar koruması)
deploy/       akim.service (systemd) · windows/ (kurulum, çalıştırma, otomatik başlatma) · termux/ (Android kurulum ve çalıştırma)
docs/         windows.md · android-hyperos.md · termux.md
eval/         etiketli gerçek veri, değerlendirme betiği ve sonuçlar (bkz. eval/README.md)
```
