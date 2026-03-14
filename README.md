Offline LLM CAD Komut Yorumlayıcısı
===================================

Bu proje, **internet bağlantısı olmadan**, Windows iş bilgisayarında çalışan,
**doğal dil → JSON plan → 3DEXPERIENCE/CATIA komut zinciri** sunan bir CLI aracıdır.

LLM yalnızca JSON plan üretir; gerçek CAD işlemleri tamamen Python tarafındaki
`ThreeDXExecutor` sınıfı üzerinden, COM API’leriyle yapılır.


Klasör Yapısı
-------------

```text
Offline_LLMV3/
  chat.py             # Ana CLI, LLM entegrasyonu, sahne takibi
  executor.py         # Plan doğrulama + 3DEXPERIENCE COM çağrıları
  prompts.py          # LLM sistem prompt’ları (JSON şemaları, örnekler)
  preparser.py        # Türkçe komut ön-işleme (regex), fallback plan üretimi
  json_grammar.py     # GBNF grameri (LLM’yi geçerli JSON üretmeye zorlar)
  run.bat             # Windows başlatıcı (simülasyon / gerçek 3DX modları)
  ornek_komutlar.txt  # Örnek doğal dil komutlar
  models/
    qwen2.5-1.5b-instruct-q4_k_m.gguf  # Yerel LLM modeli (Qwen 2.5 1.5B)
  python/
    python.exe          # Embedded Windows Python 3.12 (x64)
    Lib/site-packages/  # Gerekli tüm paketler (llama_cpp, numpy, jinja2, pywin32, ...)
```

> **GitHub repo notu:** Bu repoda `python/` ve `models/*.gguf` `.gitignore` ile hariçtir (boyut limiti).
> Çalıştırmak için yerel kopyanızda Windows embedded Python 3.12 x64 ve bir .gguf modeli
> (ör. `models/qwen2.5-1.5b-instruct-q4_k_m.gguf`) ile `python/Lib/site-packages/` içinde
> llama_cpp, pywin32 vb. paketlerin kurulu olması gerekir.

Her şey **proje klasörünün içinde** hazır olduğu için, iş PC’nizde
ekstra pip kurulumu veya internet erişimi gerekmez.


Gereksinimler
-------------

- İşletim sistemi: **Windows 10/11, 64-bit**
- 3DEXPERIENCE/CATIA:
  - CATIA / 3DEXPERIENCE client kurulu olmalı
  - COM arayüzü (VB makroları çalışıyorsa genelde hazırdır)
  - Modelleme için bir **Part** dokümanı açık olmalı (aktif editor)
- Donanım:
  - En az 8 GB RAM (1.5B model için; ne kadar fazla, o kadar iyi)
  - CPU: 4 çekirdek önerilir (chat.py `n_threads=os.cpu_count()` kullanıyor)
  - GPU varsa llama-cpp otomatik olarak kullanmaya çalışır (Metal/CUDA vs.)

Python ve bağımlılıklar:

- `python/` klasörü içinde **embedded Python 3.12 x64** bulunur.
- `python/Lib/site-packages/` altında şu paketler **önceden kurulmuş** durumdadır:
  - `llama_cpp`
  - `numpy`
  - `jinja2`
  - `pywin32` (`win32com`, `pythoncom312.dll`, `pywintypes312.dll`)
  - `diskcache`, `markupsafe`, `typing_extensions`

Yani kurulum tarafında sizin yapmanız gereken tek şey:

1. Bu klasörü Windows iş PC’nize kopyalamak.
2. Gerekirse `models/` altındaki `.gguf` modeli güncellemek (aynı isimde olursa kodu değiştirmek gerekmez).


Çalıştırma (Kullanım Kılavuzu)
------------------------------

### 1. Simülasyon Modu (3DEXPERIENCE olmadan test)

Bu modda **hiçbir gerçek CAD komutu çalıştırılmaz**; yalnızca terminalde
ne yapılacağını gösterir. İlk testler için idealdir.

Adımlar:

1. `Offline_LLMV3` klasörünü açın.
2. `run.bat` dosyasına çift tıklayın.
3. Menüden **1 - Simulasyon modu** seçin.

Terminalde şuna benzer bir ekran görürsünüz:

```text
========================================
  Offline LLM CAD Komut Yorumlayıcısı
  Mod: SIMULATION
========================================
Türkçe komut yazın, örnek:
  orijine nokta at, 30 30 30'a bir nokta daha at, iki noktadan geçen bir line çiz
...
```

Sonra şu komutu yazabilirsiniz:

```text
Komut> orijine nokta at, 30 30 30'a bir nokta daha at, iki noktadan geçen bir line çiz
```

Beklenen çıktı (özet):

```text
[SIMULATION] 3DEXPERIENCE bağlantısı yerine terminal çıktısı kullanılıyor.
[SIMULATION] create_point P1 at (0.0, 0.0, 0.0)
[SIMULATION] create_point P2 at (30.0, 30.0, 30.0)
[SIMULATION] create_line_between_points L1 using P1, P2
  [SIMULATION] Sahnedeki toplam nesne: 3
```

Mevcut sahneyi görmek için:

```text
Komut> sahne
```

Sahneyi sıfırlamak için:

```text
Komut> sıfırla
```

Çıkmak için:

```text
Komut> q
```


### 2. Gerçek 3DEXPERIENCE Modu

Bu modda LLM’den gelen JSON plan, `ThreeDXExecutor` üzerinden
**gerçek COM çağrılarına** çevrilir ve aktif Part üzerinde geometri oluşturulur.

Adımlar:

1. Windows’ta 3DEXPERIENCE / CATIA’yı açın.
2. Yeni bir Part dokümanı oluşturun veya var olan bir Part’ı açın.
3. Part dokümanı aktif editorde açıkken (makro yazacağınız normal durum):
4. `Offline_LLMV3` klasöründe `run.bat`’a çift tıklayın.
5. Menüden **2 - Gercek 3DEXPERIENCE modu (COM baglanti)** seçin.

Başarılı bağlantıda terminal şöyle der:

```text
[INFO] 3DEXPERIENCE'a bağlanılıyor...
[OK] 3DEXPERIENCE bağlantısı kuruldu.
========================================
  Offline LLM CAD Komut Yorumlayıcısı
  Mod: 3DEXPERIENCE
...
```

Ardından aynı Türkçe komutları kullanabilirsiniz:

```text
Komut> orijine nokta at, 30 30 30'a bir nokta daha at, iki noktadan geçen bir line çiz
```

Bu sefer çıktı şöyle olacaktır:

```text
[OK] create_point P1 (0.0, 0.0, 0.0)
[OK] create_point P2 (30.0, 30.0, 30.0)
[OK] create_line_between_points L1 (P1, P2)
  [3DEXPERIENCE] Sahnedeki toplam nesne: ...
```

Ve aynı anda CATIA/3DEXPERIENCE tarafında, ilgili Part içinde **P1, P2, L1**
isimli geometrilerin oluştuğunu görürsünüz.

> Not: `create_plane`, `create_sketch`, `create_circle`, `extrude_pad`
> fonksiyonları da implement edildi; ancak bunların çalışma şekli
> kullandığınız 3DEXPERIENCE sürümüne göre ufak farklar gösterebilir.
> İlk denemeleri **simülasyon modunda** yapmanız önerilir.


Desteklenen Komutlar (Aksiyonlar)
---------------------------------

LLM’nin ürettiği JSON plan şu aksiyonları kullanır:

- `create_point(name, coordinates=[x,y,z])`
- `create_line_between_points(name, point_names=[p1,p2])`
- `create_plane(name, through_point, normal=[nx,ny,nz])`
- `create_sketch(name, on_plane)`
- `create_circle(name, center_point, radius)`
- `extrude_pad(name, from_sketch, length)`

Örnek JSON plan:

```json
{
  "intent": "cad_command",
  "operations": [
    {"action": "create_point", "name": "P1", "coordinates": [0,0,0]},
    {"action": "create_point", "name": "P2", "coordinates": [30,30,30]},
    {
      "action": "create_line_between_points",
      "name": "L1",
      "point_names": ["P1", "P2"]
    }
  ]
}
```

Siz sadece **Türkçe komut** yazıyorsunuz; bu JSON’u LLM üretiyor,
`executor.py` doğruluyor ve 3DEXPERIENCE’a güvenli şekilde uyguluyor.


Mimari Özeti
------------

1. **LLM Planner (`chat.py` + `prompts.py`)**
   - Kullanıcı Türkçe komut yazar.
   - `preparser.py` regex ile koordinat / isim / aksiyon ipuçlarını çıkarır.
   - `prompts.py` sadece ilgili aksiyon şemalarını içeren kısa bir sistem prompt üretir.
   - LLM, **GBNF grameri** sayesinde daima JSON döndürmek zorundadır.
   - `chat.py` içindeki post-processing katmanı:
     - Anahtar isimlerini düzeltir (`coordinate` → `coordinates` vs.).
     - Duplicate `operations` alanlarını birleştirir.
     - Eksik `intent` alanını tamamlar (`cad_command` vs).
     - Sahnedeki isimlerle çakışan yeni isimleri otomatik yeniden adlandırır.

2. **Validator (`executor.validate_plan`)**
   - JSON’un yapısını ve mantığını kontrol eder:
     - `intent == "cad_command"` mü?
     - `operations` listesi boş değil mi?
     - Her aksiyon için zorunlu alanlar mevcut mu?
     - Bağımlılık sırası doğru mu (nokta → düzlem → sketch → extrude)?
   - Hatalı plan için net, Türkçe hata mesajı üretir.

3. **Executor (`ThreeDXExecutor`)**
   - Sadece **onaylanmış** planları çalıştırır.
   - COM üzerinden 3DEXPERIENCE’a bağlanır (`win32com.client.Dispatch`).
   - Tüm geometri isimlerinde çakışmayı engeller (önce kendisi kontrol ediyor).

4. **Güvenlik**
   - LLM hiçbir zaman direkt COM fonksiyonu seçmiyor; sadece:
     - “`create_point` ile nokta oluştur”
     - “`create_line_between_points` ile çizgi oluştur”
   - Hangi API çağrısının yapıldığı tamamen `ThreeDXExecutor` içinde sabit.


Örnek Türkçe Komutlar
----------------------

- Nokta ve çizgi:

  ```text
  orijine nokta at, 30 30 30'a bir nokta daha at, iki noktadan geçen bir line çiz
  ```

- İsimlendirilmiş noktalar:

  ```text
  50 0 0'a PA noktasını koy, 0 50 0'a PB noktasını koy, bu iki noktadan L_AB çizgisini çiz
  ```

- Daire + extrude:

  ```text
  orijine CC noktasını koy, CC merkezli 40 mm yarıçaplı bir daire çiz ve bunu 80 mm extrude et
  ```

- Eksik bilgi (soru sormasını beklersiniz):

  ```text
  nokta at
  daire çiz
  line çiz
  ```

Bu durumlarda LLM, şu formda bir JSON üretir:

```json
{
  "intent": "clarification_needed",
  "message": "Noktanın koordinatlarını belirtir misiniz? Örnek: 10 20 30"
}
```

ve CLI size `[SORU] ...` şeklinde bir soru gösterir.


İş PC’sinde Hata Almamak İçin Kontrol Listesi
---------------------------------------------

Windows iş bilgisayarına geçmeden önce bu listeyi gözden geçirin:

1. **Klasör bütünlüğü**
   - `Offline_LLMV3` klasörünü **tek parça** halinde kopyalayın.
   - İçinde şu dosyaların olduğundan emin olun:
     - `chat.py`, `executor.py`, `prompts.py`, `preparser.py`, `json_grammar.py`
     - `run.bat`
     - `models/qwen2.5-1.5b-instruct-q4_k_m.gguf`
     - `python/python.exe`
     - `python/Lib/site-packages/llama_cpp/`
     - `python/Lib/site-packages/win32com/`
     - `python/pythoncom312.dll`, `python/pywintypes312.dll`

2. **İnternet bağımlılığı**
   - Projede **pip ile paket indiren hiçbir kod kalmadı**.
   - `get-pip.py` ve `setup_deps.bat` silindi.
   - Tüm bağımlılıklar `python/Lib/site-packages/` içinde hazır.

3. **3DEXPERIENCE bağlantısı**
   - `run.bat → 2 - Gercek 3DEXPERIENCE modu` seçildiğinde:
     - Eğer pywin32 veya COM tarafında sorun varsa, hata mesajı:
       - `3DEXPERIENCE bağlantısı kurulamadı: ...`
       - Ardından otomatik olarak **SIMULATION** moduna döner.
     - Yani iş PC’sinde bile “sert” bir çökme beklemiyoruz; en kötü durumda
       simülasyon moduna düşüp sadece terminal çıktısı üretir.

4. **Model dosyası**
   - `models/qwen2.5-1.5b-instruct-q4_k_m.gguf` yoksa,
     `chat.py` başlarken:

     ```text
     FileNotFoundError: models/ klasöründe .gguf dosyası bulunamadı.
     ```

   - Bu durumda aynı klasöre bir `.gguf` koyabilir, isterseniz
     `DEFAULT_MODEL` sabitini (`chat.py` içinde) değiştirerek dosya adını uyarlayabilirsiniz.


Özet
----

- Proje, **tamamen offline**, **taşınabilir** bir CLI tabanlı CAD komut yorumlayıcısıdır.
- 1.5B Qwen modeli ile çalışacak şekilde optimize edildi (dinamik prompt, GBNF, post-processing vb.).
- İş PC’sinde yalnızca:
  - `run.bat` → Simülasyon modunu test etmeniz,
  - Sonra `run.bat` → Gerçek 3DEXPERIENCE moduna geçmeniz yeterli.

Bu README ve mevcut proje haliyle, Windows iş bilgisayarında
ek bir kurulum adımı olmadan (internet gerekmeksizin) çalışacak şekilde
son kez gözden geçirilmiş durumdadır.

