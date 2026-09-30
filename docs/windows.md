# Windows'ta kurulum ve 7/24 çalıştırma

Windows 10/11, Python 3.10+ gerekir ([python.org](https://www.python.org/downloads/); kurarken **Add python.exe to PATH** kutusunu işaretle).

## Kurulum (yönetici yetkisi gerekmez)

1. Depoyu indir/klonla, klasörde `deploy\windows\install.cmd` dosyasına çift tıkla.
   Sanal ortam (`.venv`), bağımlılıklar, `config.yaml` ve `.env` şablonları oluşur. Yerel Laya için: `install.cmd -WithLaya`.
2. `.env` dosyasını Not Defteri ile aç, en az bir bildirim kanalı gir (Telegram veya ntfy; bkz. [telefon kurulumu](android-hyperos.md)).
   Not Defteri'nin "UTF-8 (BOM)" kaydı sorun çıkarmaz.
3. Dene: `.venv\Scripts\akim.exe test-notify`

## Çalıştırma

| Yol | Ne zaman |
|---|---|
| `deploy\windows\run.cmd` | Konsolu açık tutarak. Hata ile durursa 15 sn sonra kendiliğinden yeniden başlar; **Ctrl+C** temiz kapanır ve yeniden başlatmaz |
| `powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1` | Oturum açılınca görünmez (konsolsuz) başlar; Görev Zamanlayıcı, hata ile çıkarsa 1 dk aralıkla yeniden başlatacak şekilde ayarlanır. Log: `data\akim.log` |
| `autostart.ps1 -Status` / `-Remove` | Görev durumu / kaldırma |

Panel: <http://localhost:8080>. Uygulama olarak yüklemek için Edge/Chrome adres çubuğundaki **Yükle** simgesi.

## Bilgisayarın uyumaması

`general.keep_awake` (varsayılan açık) Windows'a "sistem meşgul" bilgisi verir; **boşta uyku** engellenir, ekran kapanabilir.
Şunları engellemez: kapağı kapatmak (Güç seçenekleri → "Kapağı kapattığımda: Hiçbir şey yapma" ayarla), Başlat'tan elle Uyku/Hazırda Beklet, Windows Update yeniden başlatması.
Görev oturum açılınca başladığından yeniden başlatmadan sonra bir kez oturum açman gerekir. **Gerçek 7/24 için bir sunucu/VPS ve Docker daha doğru seçimdir**; PC yalnızca kapalı olmadığı sürece izler.

## Telefondan panele erişim

Sunucu açılışında log'a `Telefondan (aynı Wi-Fi): http://192.168.x.x:8080` yazar. İlk çalıştırmada Windows Güvenlik Duvarı izin sorar:
**Özel ağlar**'ı işaretle. Sonradan elle açmak için (yönetici PowerShell):

```powershell
New-NetFirewallRule -DisplayName "Akim Panel" -Direction Inbound -Protocol TCP -LocalPort 8080 -Profile Private -Action Allow
```

Panelin okuma uçları (tablo, bildirim akışı) kimlik istemez. **Kontrol/Yeniden tara** gibi yazma uçları yalnızca `AKIM_WEB_TOKEN` doluysa `Bearer` ister;
boşsa ağdaki herkes bunları çağırabilir. Güvenmediğin bir Wi-Fi'deysen token ayarla.
Yalnızca bu bilgisayardan erişmek için `config.yaml` → `web.host: 127.0.0.1`. Portu internete açma; uzaktan erişim için VPN (ör. Tailscale) kullan.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `Web paneli başlatılamadı (port 8080 dolu olabilir)` | `config.yaml` → `web.port: 8088`. İzleme ve bildirimler panelsiz sürer |
| Görev çalışıyor ama bildirim yok | `data\akim.log` dosyasına bak; `.venv\Scripts\akim.exe test-notify` çalıştır |
| Konsolda Türkçe karakter/emoji kutu görünüyor | Windows Terminal veya yazı tipi sorunu; çıktı UTF-8'dir, log dosyası etkilenmez |
| `running scripts is disabled` | Betikleri `powershell -ExecutionPolicy Bypass -File …` ile çalıştır (kalıcı ayar değişmez) |
| `Python 3.10 veya üstü bulunamadı` | python.org'dan kur; Microsoft Store'un boş `python.exe` kısayolu sayılmaz |
