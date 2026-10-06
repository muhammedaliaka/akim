# Android'de Termux ile kurulum (sunucu telefonda)

Akım'ın APK'sı yoktur; Python uygulamasıdır. Telefonda [Termux](https://github.com/termux/termux-app) içinde çalışır. Tamamı ücretsizdir.

> **Dürüst uyarı:** Android 12 ve sonrası, Termux'un alt süreçlerini (Python dahil) sistem düzeyinde öldürebilir ("Phantom Process Killer"); HyperOS/MIUI ayrıca agresif arka plan kısıtlamalarıyla bilinir.
> Aşağıdaki ayarlar riski azaltır, sıfırlamaz. Sürekli açık bir PC/Linux makine varsa sunucuyu orada çalıştırıp telefonu istemci yapmak daha güvenlidir ([android-hyperos.md](android-hyperos.md)).
> Bu adımlar gerçek bir Android/HyperOS cihazda denenmedi; Linux'ta derleyicisiz kurulum ve çalıştırma akışı denendi.

## 1. Termux'u kur

[F-Droid](https://f-droid.org/en/packages/com.termux/) veya [GitHub Releases](https://github.com/termux/termux-app/releases) üzerinden kur (Termux geliştiricilerinin önerdiği kaynaklar).
Google Play sürümünün F-Droid'e göre eksik ve hatalı olduğu belirtiliyor. Farklı kaynakların imzaları farklıdır; Termux eklentilerini de aynı kaynaktan kur.

## 2. Akım'ı kur

```bash
pkg update -y && pkg install -y git
git clone https://github.com/muhammedaliaka/akim && cd akim
sh deploy/termux/install.sh
```

Betik `python`, `tmux`, `nano` paketlerini kurar, sanal ortam oluşturur ve `config.yaml` ile `.env` şablonlarını kopyalar.
Android'de derleyici olmadığından aiohttp ve bağımlılıkları saf-Python modunda kurulur (betik bunu kendisi ayarlar).

## 3. Bildirimi ayarla ve dene

```bash
nano .env                    # AKIM_NTFY_TOPIC=akim-<tahmin edilemez bir şey>  (veya Telegram bilgileri)
.venv/bin/akim test-notify   # telefona bildirim düşmeli
```

Bildirimleri telefonda göstermek için ntfy uygulamasını kur ve aynı konuya abone ol ([android-hyperos.md](android-hyperos.md) bölüm 1).

## 4. Çalıştır

```bash
tmux new -s akim
sh deploy/termux/run.sh      # tmux'tan ayrılmak için Ctrl+B, sonra D; geri dönmek için: tmux attach -t akim
```

`run.sh` uykuyu engeller (`termux-wake-lock`), hata ile durursa 15 sn sonra yeniden başlatır; Ctrl+C temiz kapanır.
Termux'u yeniden başlatırsan (telefon açılışı dahil) bu adımı elle tekrarla.

Paneli aynı telefonda aç (varsayılan olarak yalnızca bu telefondan erişilir; başka cihazlar için `.env` içine `AKIM_WEB_HOST=0.0.0.0` ve `AKIM_WEB_TOKEN` yaz): Chrome'da `http://localhost:8080` → menü → **Uygulamayı yükle**. `localhost` güvenli bağlam sayıldığından tam PWA çalışır.

## 5. Roblox erişimi (VPN)

Roblox'a erişimin engelli olduğu bir ağdaysan telefonda VPN açık olmalı. Bölünmüş tünel (split tunneling) Termux'u dışarıda bırakmıyorsa VPN Termux trafiğini de kapsar.
VPN kapanırsa Akım durmaz: Roblox'suz "yükseliyor" raporu verir ve VPN açılınca kendiliğinden doğrular. Kesinti 10 dakikayı aşarsa tek bir uyarı gelir.
Son günlerde ne olduğunu görmek için: `.venv/bin/akim report`.

## 6. Öldürülmeyi azalt

1. **HyperOS:** Termux'u son uygulamalarda kilitle; Ayarlar → Uygulamalar → Termux → "Arka planda otomatik başlatma" açık, pil kısıtlaması **Kısıtlama yok** ([android-hyperos.md](android-hyperos.md) bölüm 2).
2. **Phantom Process Killer** ([kaynak](https://github.com/agnostic-apollo/Android-Docs/blob/master/en/docs/apps/processes/phantom-cached-and-empty-processes.md)):
   - Android 14 ve üstü: Ayarlar → Sistem → Geliştirici seçenekleri → **Alt işlem kısıtlamalarını devre dışı bırak** (üreticiye göre bulunmayabilir).
   - Android 12L/13 (veya yukarıdaki anahtar yoksa), bilgisayardan ADB ile: `adb shell "settings put global settings_enable_monitor_phantom_procs false"`
   - Android 12: `adb shell "/system/bin/device_config put activity_manager max_phantom_processes 2147483647"`. Android 12'de aşırı CPU kullanan süreçlerin öldürülmesini engelleyen bir ayar yoktur.
3. **Doğrula:** ekranı kapat, 30 dakika bekle. Sonra Termux'a dönüp `tmux attach -t akim` ile Akım'ın hâlâ çalıştığını gör; tmux içinde Ctrl+B, C ile yeni pencere açıp `.venv/bin/akim test-notify` çalıştır. Öldüyse yukarıdaki ayarları gözden geçir.

## Güncelleme

```bash
cd akim && git pull && .venv/bin/python -m pip install -e .
```

`config.yaml` ve `.env` dosyaların git'e dahil değildir, korunur.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `pip install` derleme hatası veriyor | Aynı Termux oturumunda `sh deploy/termux/install.sh` çalıştır (derlemeyi atlatan değişkenleri o ayarlar). Hata sürerse çıktıyı paylaş |
| `Web paneli başlatılamadı (port 8080 dolu olabilir)` | `config.yaml` → `web.port: 8088` |
| Bir süre sonra Akım kendiliğinden kapanıyor | Bölüm 6; Termux'un hâlâ çalıştığını ve wake-lock'un açık olduğunu kontrol et |
| Bildirim gelmiyor | Konsol çıktısına bak (dosyaya da yazmak için `config.yaml` → `general.log_file: akim.log`); `.venv/bin/akim test-notify` çalıştır |
