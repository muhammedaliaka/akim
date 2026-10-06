# Arkadaşları bildirimlere katma (ntfy)

Arkadaşlar uygulama dışında hiçbir şey kurmaz ve hesap açmaz. Hepsi **aynı konuya** abone olur; Akım'ın yüksek öncelikli
bildirimleri hepsine aynı anda düşer.

## Sen (1 dakika)

1. Sunucuda `.env` içinde tahmin edilemez bir konu adı olduğundan emin ol (ör. `AKIM_NTFY_TOPIC=akim-4f9c2a7e1b`) ve Akım'ı yeniden başlat.
2. Şunu çalıştır ve çıkan metni arkadaşlarına WhatsApp/Telegram'dan gönder:

   ```bash
   akim invite
   ```

   Konu adın zayıfsa komut bunu söyler ve güçlü bir öneri verir.

## Arkadaşın (1 dakika)

1. **ntfy** uygulamasını kur: Android → Play Store veya F-Droid · iPhone → App Store.
2. Uygulamayı aç → **➕** ile yeni konu ekle → konu adına senin verdiğin adı yaz → **Abone ol**. (Sunucu: `ntfy.sh`, olduğu gibi kalsın.)
3. Bildirim iznini ver.
   - **Xiaomi/HyperOS:** Ayarlar → Pil → ntfy → **Kısıtlama yok**; ntfy'yi son uygulamalar ekranında kilitle.
   - Diğer Android'ler: Pil ayarlarında ntfy için "kısıtlama yok / optimize etme".

Kısayol: `akim invite` çıktısındaki `ntfy://ntfy.sh/<konu>` bağlantısına dokunmak, ntfy kuruluysa uygulamayı açıp konuya abone eder.

Dene: sunucuda `akim test-notify` çalıştır; arkadaşın telefonuna "Akım test bildirimi" düşmeli.

## Bilmen gerekenler

- **Konu adı bir paroladır.** Bilen herkes bildirimleri okuyabilir ve konuya mesaj gönderebilir. Yalnızca güvendiklerinle paylaş.
- Bir arkadaşını çıkarmak ya da konu sızarsa: `.env` içindeki `AKIM_NTFY_TOPIC`'i değiştir, Akım'ı yeniden başlat, yeni adı kalanlara gönder.
- Herkes aynı mesajı alır. Hangi bildirimlerin geleceğini `notifications.channels.ntfy.min_priority` belirler (varsayılan `high`).
  Arkadaşlar yalnızca katıldıktan sonraki bildirimleri görür; ntfy sunucuları mesajları genellikle yalnızca kısa süre (varsayılan 12 saat) saklar.
- ntfy.sh ücretsizdir ve hesap istemez. Kendi ntfy sunucun varsa `AKIM_NTFY_SERVER` ile belirt; `akim invite` metni sunucuyu da yazar.
