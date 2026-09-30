# Roblox eşleştirme değerlendirmesi

Bu klasör, Akım'ın "bu Roblox oyunu şu PC oyununun klonu mu?" kararının ne kadar doğru olduğunu
ölçer ve farklı yöntemleri (sezgisel motor, Laya, karışımlar) aynı ölçütle karşılaştırır.

## Veri

| Dosya | İçerik |
|---|---|
| `candidates.json` | 10 oyun için canlı Roblox arama sonuçları (oyun başına 40 aday). Sezgisel motor **bu set üzerinde kalibre edildi** |
| `labels.json` | Bu setten 149 elle etiketlenmiş çift |
| `candidates_holdout.json` | Kalibrasyonda **hiç kullanılmamış** 8 oyun: Content Warning, Buckshot Roulette, Liar's Bar, Balatro, Deep Rock Galactic, Vampire Survivors, Raft, Overcooked! 2 |
| `labels_holdout.json` | Bu setten 112 elle etiketlenmiş çift |
| `laya_cache_*.json` | Laya çıktıları (ölçümleri PyTorch kurmadan yeniden üretmek için) |
| `laya_dataset_*.jsonl` | Aynı çiftler, Laya'nın `laya-evals` ve ince ayar biçiminde |

Etiketler (Roblox açıklamasına bakılarak, 2026-09-30):

- **clone**: aynı çekirdek oynanış döngüsü
- **inspired**: ana fikir aynı, belirgin farklarla
- **same_genre**: yalnızca geniş tür aynı
- **unrelated**: ilgisiz

Açıklaması boş veya belirsiz adaylar etiketlenmedi. Etiketler tek kişinin yargısıdır; sınırdaki
vakalarda (ör. "Clothing Store Simulator", Supermarket Simulator'a *inspired* mı *same_genre* mı?) başkası
farklı karar verebilir.

## Metrikler

- **AUROC:** skorun klon/esinlenme adaylarını diğerlerinden ayırma gücü. 0.5 şans, 1.0 kusursuz.
- **Sıra doğruluğu:** aynı oyunun farklı dereceli adaylarında (klon > aynı tür > ilgisiz) sıranın doğru olma oranı.
- **Kesinlik / duyarlılık / F1:** sistemin "klon" dediği adayların doğruluğu. Duyarlılık düşükse klonlar kaçıyor demektir: sistem Roblox'ta zaten olan bir oyuna "FIRSAT" der.

## Sonuçlar (2026-09-30)

### Hiç görülmemiş oyunlar (holdout, 112 çift: 20 klon/esinlenme, 92 diğer)

| Yöntem | AUROC | Sıra doğr. | Kesinlik | Duyarlılık | F1 |
|---|---|---|---|---|---|
| **Akım sezgisel** (isim + atıf + oynanış) | **0.898** | **0.651** | 0.70 | 0.35 | 0.47 |
| **Akım + Laya evidence modu** (güven 0.35) | 0.897 | 0.644 | 0.53 | **0.50** | **0.51** |
| Laya: 4 seçenekli ilişki sorusu | 0.584 | 0.492 | 0.21 | 0.65 | 0.32 |
| Laya: "aynı döngü mü?" (iki seçenek) | 0.667 | 0.562 | | | |
| Laya: sıralı benzerlik skoru | 0.592 | 0.577 | | | |
| Laya: doğal dil çıkarımı kalıbı | 0.660 | 0.580 | | | |
| Laya: kodlayıcı gömme kosinüsü | 0.484 | 0.575 | | | |
| Doğrusal karışım 0.5 sezgisel + 0.5 Laya | 0.890 | 0.631 | | | |

### Kalibrasyon seti (149 çift; sezgisel motor için iyimser, örneklem içi)

| Yöntem | AUROC | Sıra doğr. | Kesinlik | Duyarlılık | F1 |
|---|---|---|---|---|---|
| Akım sezgisel | 0.982 | 0.812 | 0.97 | 0.67 | 0.79 |
| Akım + Laya evidence modu (0.35) | 0.982 | 0.782 | 0.92 | 0.80 | 0.86 |
| Laya: 4 seçenekli ilişki | 0.559 | 0.543 | 0.35 | 0.56 | 0.43 |
| Laya `typed-decisions` checkpoint, 4 seçenek | 0.599 | 0.611 | 0.33 | 0.33 | 0.33 |
| Laya: "aynı döngü mü?" | 0.698 | 0.554 | | | |

Evidence modundaki güven ağırlığı yalnızca kalibrasyon setinde seçildi; holdout ayarlamada hiç kullanılmadı:

| Güven | calib F1 | holdout F1 |
|---|---|---|
| 0 (Laya kapalı) | 0.79 | 0.47 |
| 0.20 | 0.85 | 0.51 |
| **0.35** | **0.86** | 0.51 |
| 0.50 | 0.85 | 0.51 |

### Yorum

1. **Hazır Laya bu görevde eğitimsiz kullanımda zayıf.**
   - En iyi biçimi AUROC ≈ 0.67; sezgisel motor 0.90.
   - Gerçek CS klonlarıyla genel FPS oyunlarını ayırt edemiyor.
   - Denenen İngilizce ve `typed-decisions` checkpoint'leri ile beş farklı soru biçiminin hiçbiri bunu değiştirmedi.
   - Bu, Laya'nın kendi dokümantasyonuyla tutarlı: hazır checkpoint'ler yeni karar görevlerinde eğitimsiz kullanımda şansa yakın kalabiliyor ve ince ayar öneriliyor. Bu, Laya'nın genel kalitesi hakkında bir yargı değil; yalnızca bu göreve ince ayarsız uygulandığındaki ölçüm.
2. **Evidence modu gerçek bir takas sunuyor.**
   - Laya, kavram sözlüğünde karşılığı olmayan yeni mekaniklerde (Buckshot Roulette, Raft) sezgisel motorun kaçırdığı klonların bir kısmını yakalıyor: duyarlılık 0.35'ten 0.50'ye çıkıyor.
   - Karşılığında yanlış alarm artıyor: kesinlik 0.70'ten 0.53'e iniyor.
   - "Roblox'ta zaten olan bir oyunu fırsat sanmamak" daha önemliyse açılmalı; "fırsat kaçırmamak" daha önemliyse kapalı kalmalı.
3. **Sezgisel motorun zayıf yanı yeni mekanikler.** Holdout'ta duyarlılık 0.35. Trend olan oyunlar çoğu zaman tam da yeni mekanikleri yüzünden trend olur; elle yazılmış bir kavram sözlüğü onlara hep geriden gelir.
4. **Laya'yı gerçekten güçlü yapacak yol ince ayar.**
   - `laya_dataset_calib.jsonl` ile eğitip `laya_dataset_holdout.jsonl` ile ölçmek mümkün. Laya'nın notebook'u Kaggle'ın ücretsiz T4'lerinde çalışıyor.
   - Ancak 149 örnek küçük bir eğitim seti; güvenilir bir model için birkaç yüz etiketli çift daha gerekir.
   - İnce ayarlı model `laya.model_path` ve `laya.mode: judge` ile doğrudan kullanılabilir.

### Hız

CPU'da (4 çekirdek): model yükleme 5-16 sn. Çift başına ~0.7 sn (tek soru, kısa girdi) ile ~2.7 sn (üç soru). Oyun başına 8 aday ≈ 10-20 sn, sonuçlar önbelleklenir. Laya'nın dokümantasyonuna göre GPU'da (T4) çift başına ~35 ms.

## Yeniden üretmek

```bash
python eval/run_eval.py --set holdout              # yalnızca sezgisel (saniyeler)
python eval/run_eval.py --set holdout --laya --nli  # Laya dahil; önbellek varsa model çalışmaz
python eval/export_laya_dataset.py                  # Laya JSONL biçimine aktar
laya-evals run eval/laya_dataset_holdout.jsonl --model english
```

Kavram sözlüğünde yapılan her değişiklik holdout'ta yeniden ölçülmeli. Holdout'a bakarak sözlük ayarlanırsa artık holdout değildir; yeni bir holdout seti toplanmalı.
