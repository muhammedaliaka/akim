# Android ve HyperOS (Xiaomi) telefon kurulumu

## Mimari: sunucu PC'de, telefon istemci

Akım 7/24 çalışan bir süreçtir. **Bunu telefonda çalıştırmak riskli**: Android 12 ve sonrası Termux gibi uygulamaların alt süreçlerini sistem düzeyinde öldürebiliyor
("Phantom Process Killer"; wake-lock ve pil ayarları tek başına bunu engellemiyor). Xiaomi'nin HyperOS/MIUI'si ayrıca agresif arka plan kısıtlamalarıyla bilinir.
Yine de telefonda çalıştırmak istersen: [termux.md](termux.md). Daha güvenli düzen:

```
Windows PC / Linux makine (Docker)        ──►  ntfy ve/veya Telegram  ──►  Android telefon (HyperOS)
         Akım burada 7/24 çalışır               anlık push                   bildirim + panel (PWA)
```

Telefonda yapılacaklar: bildirim uygulaması kurmak, **HyperOS'un o uygulamayı öldürmemesini sağlamak**, paneli ana ekrana eklemek.

## 1. Bildirim uygulaması

| Kanal | Kurulum | HyperOS için not |
|---|---|---|
| **ntfy** (önerilen, anlık push) | Uygulamayı kur, tahmin edilemez bir konuya abone ol (ör. `akim-4f9c2a7e`), sunucuda `AKIM_NTFY_TOPIC` ayarla | F-Droid sürümü Firebase kullanmaz ve **anında teslim** (ön plan hizmeti) ile çalışır. Google Play sürümü `ntfy.sh` konularında Firebase kullanır; ntfy belgeleri Firebase'in teslimatının gecikebildiğini söyler. Kendi ntfy sunucun varsa her iki sürüm de anında teslim kullanır |
| **Telegram** | [@BotFather](https://t.me/BotFather) ile bot, `AKIM_TELEGRAM_BOT_TOKEN` + `AKIM_TELEGRAM_CHAT_ID` | Aşağıdaki HyperOS ayarları Telegram için de geçerli. Bildirimle birlikte `/kontrol <oyun>` gibi komutlar da çalışır |

Akım'ın ntfy kanalının varsayılan eşiği `high`: yalnızca yüksek (ntfy önceliği 4) ve kritik (5) fırsatlar telefona düşer (`min_priority` ile değiştirilir).
ntfy yüksek ve acil bildirimleri açılır (pop-over) gösterir ve öncelik başına ayrı bildirim kanalı sunar
(uygulama → Ayarlar → kanal ayarları; buradan "Rahatsız Etme'yi geçersiz kıl" seçilebilir).

## 2. HyperOS'un uygulamayı öldürmesini engelle

Xiaomi'nin MIUI/HyperOS'u arka plan uygulamalarını varsayılan olarak agresif kapatır. **ntfy** (ve kullanıyorsan **Telegram**) için şunları yap.
Menü adları HyperOS/MIUI sürümüne ve dile göre değişir; [dontkillmyapp.com/xiaomi](https://dontkillmyapp.com/xiaomi) MIUI için aşağıdaki yolları listeler, HyperOS'ta benzer adlarla bulunur:

1. **Son uygulamalar ekranında uygulamayı kilitle**: son uygulamalar görünümünde uygulama kartını aşağı çek (kilit simgesi çıkar).
2. **Otomatik başlatma**: Ayarlar → Uygulamalar → (uygulama) → Uygulama izinleri → Arka planda otomatik başlatma → açık. (Eski yol: Güvenlik uygulaması → İzinler → Otomatik başlatma.)
3. **Pil kısıtlaması yok**: Güvenlik uygulaması → Pil → Uygulama pil tasarrufu → (uygulama) → **Kısıtlama yok**. (Eski yol: Ayarlar → Pil ve performans → Uygulamaların pil kullanımını yönet.)
4. **Bildirim izinleri**: Ayarlar → Bildirimler → (uygulama) → bildirimlere izin ver, **Kilit ekranında göster** ve **Kayan bildirimler** açık olsun; ntfy'nin yüksek/acil öncelikli bildirim kanallarını kapatma.
5. Mümkünse pil tasarrufu modunu sürekli açık tutma; "Ultra tasarruf" arka plan bağlantılarını keser.

## 3. Doğrula (5 dakika)

Ayarları yapınca **gerçekten işe yaradığını** şöyle test et; Android cihazlar arasında fark büyük:

1. Sunucuda `akim test-notify` çalıştır: telefona saniyeler içinde bildirim düşmeli.
2. Telefonun ekranını kapat, ntfy/Telegram'ı son uygulamalardan kaldırma, **20-30 dakika bekle**.
3. Tekrar `akim test-notify` çalıştır. Bildirim ses/titreşimle ve hâlâ saniyeler içinde geldiyse ayar tamamdır.
4. Gecikmeli geliyorsa ya da sadece telefonu açınca düşüyorsa: 2. ve 3. adımları gözden geçir; ntfy'de F-Droid sürümüne veya kendi ntfy sunucuna geç.

## 4. Paneli telefona ekle

Panel varsayılan olarak yalnızca sunucu makinesinden açılır. Telefondan açmak için sunucuda `.env` içine `AKIM_WEB_HOST=0.0.0.0` ve güçlü bir `AKIM_WEB_TOKEN` yaz
(ayrıntı: [windows.md](windows.md#telefondan-panele-erişim)); yeniden başlatınca log'a `Telefondan (aynı Wi-Fi): http://192.168.x.x:8080` yazar.
Chrome/Mi Tarayıcı'da aç → menü → **Ana ekrana ekle** (veya **Uygulamayı yükle**). Token olmadan panel telefonda yalnızca okunur.

- Panel telefonda tek sütun, büyük dokunma hedefleri, çentik/çene güvenli alanları ve açık/koyu temayla çalışır; ekran kilidinden dönünce canlı bağlantıyı kendi yeniler.
- Düz `http://` (HTTPS olmayan yerel adres) üzerinde tarayıcı servis çalışanını çalıştırmaz: ana ekran kısayolu çalışır, tam "uygulama" kabuğu ve çevrimdışı açılış yalnızca HTTPS/`localhost` üzerinde olur.
- Ev dışından erişim için VPN (ör. Tailscale) kullan; panel portunu internete açma.
- **Bildirimler panelden değil ntfy/Telegram'dan gelir.** Tarayıcı kapalıyken panel bildirim gösteremez.

## Sınırlar

- Bu repodaki testler Android cihazda ve HyperOS'ta çalıştırılmadı; bu belge ntfy/dontkillmyapp belgelerine ve Android davranışına dayanır. Yukarıdaki doğrulama adımı senin cihazın için gerçek testtir.
- Panel, Android Chrome davranışıyla (emüle edilmiş Pixel/Redmi boyutları) denendi: yatay taşma yok, dokunma hedefleri ≥44 px, Chrome'un yüklenebilirlik denetimi geçiyor. Mi Tarayıcı ayrıca denenmedi.

Kaynaklar: [ntfy: telefon uygulaması](https://docs.ntfy.sh/subscribe/phone/) · [dontkillmyapp: Xiaomi](https://dontkillmyapp.com/xiaomi) · [Termux: Android 15'te süreçlerin öldürülmesi](https://github.com/termux/termux-app/issues/5150)
