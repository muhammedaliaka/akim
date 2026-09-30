# Akım — Steam & Epic → Roblox fırsat radarı

Akım, 7/24 açık kalan bir izleme servisidir:

1. **Steam** ve **Epic Games Store** listelerini (en çok satanlar, en çok oynananlar, trend, yeni çıkanlar…) sürekli çeker.
2. Büyük markaları (EA, Ubisoft, Sony, Tencent…) eler; **az bilinen / bağımsız yapımcıların** yükselen oyunlarını bulur.
3. Her adayı **Roblox'ta arar**. İsim benzerliğine ve açıklamalardaki *"inspired by PEAK"* gibi ifadelere bakarak klonları tespit eder.
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
python -m venv .venv && . .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
akim run                  # veya: python -m akim run
```

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

- Roblox eşleştirmesi **isim ve açıklamaya** dayanır. Farklı isimli konsept klonlar yalnızca açıklamada orijinal oyunu anıyorsa yakalanır. Örneğin "CLIMB", PEAK'in bir klonudur ama açıklamada PEAK'i anmadığı için eşleşme sayılmaz. Bu tür oyunlar panelde "Roblox aramasının önerdikleri" başlığında ayrıca listelenir.
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
  analysis/   indie.py · matching.py · scoring.py  bağımsızlık, Roblox eşleştirme, karar
  notify/     telegram.py · channels.py           bildirim kanalları + dağıtıcı
  web/        server.py · dashboard.html          canlı panel ve API
  engine.py   döngüler, karar geçişleri, bildirim üretimi
  storage.py  SQLite (sıra geçmişi, Roblox geçmişi, kararlar, bildirim tekrar koruması)
```
