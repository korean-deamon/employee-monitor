# Texnik topshiriq — Xodimlar davomati va faoliyat monitoringi

| | |
|---|---|
| **Loyiha** | employee-monitor |
| **Versiya** | 2.0 |
| **Sana** | 2026-10-19 |
| **Holati** | Kelishuvga tayyor |

---

## 1. Loyihaning maqsadi

Kompaniya xodimlarining ish vaqtini kamera orqali avtomatik hisobga olish:

- xodim **qachon keldi** va **qachon ketdi** — qo'lda belgilashsiz
- **tanaffus va erta ketish** sababini xodim o'zi Telegram orqali belgilaydi
- rahbariyat uchun **kunlik va oylik hisobot**
- xona kamerasi orqali **ish joyida borligi** va **faoliyat holati**

### 1.1 Nima uchun kamera

Barmoq izi yoki karta tizimidan farqi: qo'shimcha jihoz tarqatilmaydi, karta yo'qolmaydi, bir xodim boshqasining kartasini bosib qo'yolmaydi. Xodim shunchaki kameraga qarab turadi.

### 1.2 Loyiha hajmiga kirmaydigan narsalar

- Ish haqini hisoblash
- Kadrlar bo'limi tizimi bilan integratsiya (keyingi bosqichlarda ko'rib chiqilishi mumkin)
- Video arxivini uzoq muddat saqlash (xavfsizlik kamerasi vazifasini bajarmaydi)

---

## 2. Atamalar

| Atama | Ma'nosi |
|---|---|
| **Ataylab turish** | Xodim kameraga yaqin kelib, 3 sekund joyida turishi. Davomat shu bilan belgilanadi |
| **Ko'rinish** | Kamera xodimni tanigan har qanday payt (yurib o'tgan bo'lsa ham) |
| **Keldi / Ketdi** | Bazaga yozilgan davomat hodisasi |
| **Bog'lanish kodi** | Xodimning Telegram akkauntini tizimga ulash uchun bir martalik kod |
| **Embedding** | Yuzning 512 raqamli matematik tavsifi. Rasm emas, qayta tiklanmaydi |

---

## 3. Tizim tuzilishi

```
┌──────────────────┐     ┌──────────────────┐
│  Kirish kamerasi │     │  Xona kamerasi   │
│  (RTSP, 1080p)   │     │  (RTSP, 1080p)   │
└────────┬─────────┘     └────────┬─────────┘
         │                        │
         ▼                        ▼
┌──────────────────┐     ┌──────────────────┐
│  entrance.py     │     │  room.py         │
│  yuz tanish      │     │  holat tahlili   │
└────────┬─────────┘     └────────┬─────────┘
         │                        │
         └───────────┬────────────┘
                     ▼
            ┌─────────────────┐
            │  SQLite (WAL)   │
            └────────┬────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌─────────┐  ┌─────────┐  ┌─────────┐
   │   API   │  │ Telegram│  │ Hisobot │
   │ FastAPI │  │   bot   │  │         │
   └────┬────┘  └────┬────┘  └─────────┘
        │            │
        ▼            ▼
   Dashboard     Xodimlar
   + Admin       telefoni
```

**Muhim qoida:** har bir kamerani faqat bitta jarayon ochadi. Bitta qurilmani ikki jarayon ocholmaydi.

### 3.1 Vazifalarning ajratilishi

| Komponent | Vazifasi |
|---|---|
| `entrance.py` | Kirish kamerasi. Yuz tanish, ataylab turishni aniqlash, davomat yozish |
| `room.py` | Xona kamerasi. Ish joyi zonalari, faoliyat holati. **Davomatga yozmaydi** |
| `api.py` | Dashboard, admin panel, hisobotlar |
| `bot.py` | Telegram: bog'lanish, xabarlar, tugmalar |

**Davomatni faqat kirish kamerasi yozadi.** Xona kamerasi faqat tekshiradi. Aks holda xodim monitorga o'girilganda soxta "ketdi" yozilib ketadi.

---

## 4. Jihozlarga talablar

### 4.1 Kameralar

| Talab | Kirish kamerasi | Xona kamerasi |
|---|---|---|
| Turi | IP kamera, RTSP | IP kamera, RTSP |
| Ruxsat | 1080p (minimum) | 1080p (minimum) |
| Kadr tezligi | 25 fps | 15 fps yetadi |
| PTZ (burilish) | **Kerak emas** | **Kerak emas** |
| Balandligi | **1.5–1.7 m**, yuz balandligida | Burchakda, 2.2–2.8 m |
| Yo'nalishi | **Odam yurib kelayotgan tomonga**, yondan emas | Barcha ish joylarini ko'radigan |
| Yorug'lik | Deraza kameraga qarshi bo'lmasligi kerak | — |

> **Diqqat:** kirish kamerasining balandligi hal qiluvchi ahamiyatga ega. Shiftdan pastga qaragan kamera yuz tanish aniqligini keskin tushiradi. O'rnatuvchilarga buni oldindan aytish kerak — keyin ko'chirish qiyin.

### 4.2 Server

| Talab | Qiymat |
|---|---|
| GPU | NVIDIA, CUDA qo'llab-quvvatlashi bilan |
| O'lchangan ish unumi | RTX 3050 6GB da 49 ms/kadr |
| Operativ xotira | 8 GB minimum |
| Disk | 100 GB (baza + yuz namunalari) |
| Tarmoq | Kameralar bilan bir xil lokal tarmoqda |
| Elektr | **UPS tavsiya etiladi** — elektr o'chsa davomat yozilmay qoladi |

### 4.3 Tarmoq

- Kameralar va server bir xil lokal tarmoqda
- Kameralarning RTSP manzili va paroli kerak
- Internet faqat Telegram uchun kerak (davomat yozish internetsiz ham ishlaydi)

---

## 5. Funksional talablar

### 5.1 Xodimni ro'yxatga olish

**Holati: bajarilgan (V1)**

Admin panelda:

1. Ism, Familiya, Lavozim kiritiladi
2. "Qo'shish va yuzini skanerlash" bosiladi
3. Tizim xodimni **burchaklar bo'yicha** yo'naltiradi:

| Burchak | Namuna soni |
|---|---|
| To'g'riga qarang | 2 |
| Boshingizni chapga buring | 2 |
| Boshingizni o'ngga buring | 2 |
| Biroz yuqoriga qarang | 1 |
| Biroz pastga qarang | 1 |
| **Jami** | **8** |

**Nega burchak bo'yicha:** aniqlik namuna *soniga* emas, qamrab olingan **burchaklar xilma-xilligiga** bog'liq. Bir joyda turib olingan 8 namuna amalda bitta namunaga teng.

**Namuna rad etilish shartlari:**

| Shart | Xabar |
|---|---|
| `det_score < 0.60` | "Yuz aniq ko'rinmayapti" |
| Yuz kengligi `< 70 px` | "Kameraga yaqinroq keling" |
| Mavjud namunaga `> 0.93` o'xshash | "Boshingizni biroz ko'proq buring" |
| Mavjud namunaga `< 0.40` o'xshash | "Boshqa odam ko'rinyapti" |

**Takroriy yuz nazorati:** birinchi namuna saqlanishidan **oldin** yangi yuz bazadagi barcha namunalar bilan solishtiriladi. O'xshashlik `0.40` dan yuqori bo'lsa ro'yxatga olish rad etiladi:

> Bu yuz allaqachon ro'yxatda: Ali Valiyev (o'xshashlik 0.85)

Bu zarur, chunki bir odam ikki ism bilan kiritilsa, tizim ikki yozuv orasida almashib qoladi va davomat buziladi.

**Tahrirlash:** ism, familiya, lavozimni o'zgartirish yuz namunalariga va davomat tarixiga tegmaydi.

### 5.2 Keldi aniqlash

**Asosiy yo'l — ataylab turish:**

Uch shart birga bajarilishi kerak:

| Shart | Qiymat |
|---|---|
| Yuz kengligi | `≥ 180 px` (~1.5 m masofa, sozlanadi) |
| Turish davomiyligi | `≥ 3 sekund` (sozlanadi) |
| Joyida turishi | Yuz markazi `< 15%` kadr kengligida siljishi |

Uch kadrdan kamida ikkitasi bir xil xodimni ko'rsatishi kerak (**ovoz berish**) — bitta kadrdagi xato natijaga ta'sir qilmaydi.

**Zaxira yo'l:** xodim ertalab turishni esdan chiqarsa, **bugungi birinchi ko'rinishi** `keldi` deb yoziladi (`source = first_sighting`).

**Takroriy yozuv yo'q:** `keldi` yozilgandan keyin 60 sekund ichidagi yangi turish hisobga olinmaydi.

### 5.3 Ketdi aniqlash

**Asosiy yo'l:** xodim ketishdan oldin kameraga kelib turadi (5.2 dagi uchala shart bilan).

**Zaxira yo'l:** xodim turishni esdan chiqarsa va **30 daqiqa** ko'rinmasa, tizim **oxirgi ko'rinish vaqtini** olib kunni o'zi yopadi (`source = auto_close`) va xodimga tasdiqlash xabari yuboradi.

**Hech kimning kuni ochiq qolmaydi.**

### 5.4 Vaqt qoidalari

Soat davomatni **aniqlamaydi**, faqat **baholaydi**. Kamera doim yoqilgan.

| Vaqt | Ta'siri |
|---|---|
| `09:00` gacha kelgan | Erta. Yoziladi, "N daqiqa erta" deb ko'rsatiladi |
| `09:00` dan keyin kelgan | Kechikish. Yoziladi, "N daqiqa kechikish" deb ko'rsatiladi |
| `18:00` dan keyin ketgan | Normal. Savol berilmaydi |
| `18:00` dan oldin ketgan | **Savol beriladi** (5.6.3) |

> **Nega kamera doim yoqilgan:** agar kamera 09:00 dan oldin o'chiq bo'lsa, 08:45 da kelgan xodimning kelgani yozilmaydi va u jazolangan bo'ladi. Shuningdek 19:30 gacha ishlagan xodimning ketgani yozilmaydi.

Soat qiymatlari sozlamadan o'zgartiriladi (smena uchun).

### 5.5 Telegram bog'lanish (pairing)

Har bir xodim o'z Telegram akkauntini tizimga **bir marta** ulaydi.

#### 5.5.1 Jarayon

```
1. ADMIN                 Admin panelda xodimni tanlaydi
                         → "Telegram kodini olish" bosadi
                         → Ekranda kod chiqadi:  K7M4-RPQX

2. ADMIN → XODIM         Kodni xodimga beradi
                         (og'zaki, qog'ozda yoki xabar bilan)

3. XODIM                 Botni ochadi, /start bosadi
                         ← Bot: "Salom! Davomat tizimiga ulanish
                                 uchun admin bergan kodni yuboring."

4. XODIM                 Kodni yuboradi:  K7M4-RPQX

5. TIZIM                 Kodni tekshiradi:
                         - mavjudmi
                         - muddati o'tmaganmi
                         - ishlatilmaganmi
                         - bu Telegram ID boshqa xodimga ulanmaganmi

6. TIZIM                 telegram_id ↔ employee_id bog'lanadi
                         Kod ishlatilgan deb belgilanadi
                         ← Bot: "✓ Ulandingiz.
                                 Siz: Ali Valiyev · Sotuv bo'limi"

7. ADMIN                 Admin panelda ko'rinadi:
                         Telegram: ulangan ✓
```

#### 5.5.2 Kodga talablar

| Talab | Qiymat | Sababi |
|---|---|---|
| Uzunligi | 8 belgi, `K7M4-RPQX` ko'rinishida | Og'zaki aytishga qulay |
| Alifbo | Raqam + harf, `0 O 1 I L` **ishlatilmaydi** | Adashtirmaslik uchun |
| Muddati | 24 soat | Admin qog'ozda berishi mumkin |
| Ishlatilishi | **Bir marta** | Qayta ishlatib bo'lmaydi |
| Xato urinish | Bir Telegram ID uchun soatda **5 marta** | Kodni taxmin qilishning oldini olish |

#### 5.5.3 Qo'shimcha holatlar

| Holat | Tizim nima qiladi |
|---|---|
| Xodim telefonini almashtirdi | Admin yangi kod beradi, eski bog'lanish o'chadi |
| Telegram ID allaqachon boshqa xodimga ulangan | Rad etiladi: "Bu akkaunt allaqachon ulangan" |
| Bog'lanmagan odam botga yozdi | Faqat "kodni yuboring" deb javob beradi, boshqa hech narsa ko'rsatmaydi |
| Xodim ishdan bo'shadi | Admin bog'lanishni uzadi, xabarlar to'xtaydi |

#### 5.5.4 Bot buyruqlari

| Buyruq | Vazifasi |
|---|---|
| `/start` | Salomlashish. Bog'lanmagan bo'lsa kod so'raydi, bog'langan bo'lsa holatni ko'rsatadi |
| `/bugun` | Bugungi yozuv: keldi, ketdi, tanaffuslar, jami soat |
| `/hafta` | Shu haftaning kunlik jadvali va jami soati |
| `/help` | Yordam |

> Xodim o'z ma'lumotini istagan vaqtda ko'rishi — bu shaffoflik talabi, shikoyatlarning oldini oladi.

### 5.6 Telegram xabarlari

#### 5.6.1 Keldi

```
✓ Keldingiz · 08:47
   13 daqiqa erta
```

```
✓ Keldingiz · 09:17
   17 daqiqa kechikish
```

Takroriy turishda:

```
● Allaqachon yozilgan · 08:47
```

#### 5.6.2 Ketdi — 18:00 dan keyin

Savol berilmaydi, ish tugagani aniq:

```
✓ Ketdingiz · 18:10
   Bugun: 08:47 – 18:10
   Jami: 8 soat 31 daqiqa
   Tanaffus: 52 daqiqa
```

#### 5.6.3 Ketdi — 18:00 dan oldin

```
   Ketdingizmi? · 15:20

   [ Ha ]   [ Yo'q ]   [ Sabab ]
```

| Tugma | Natija |
|---|---|
| **Ha** | Ketdi deb yoziladi, sababi ko'rsatilmagan. Hisobotda *"sababsiz erta ketish"* |
| **Yo'q** | Yozuv **bekor qilinadi**, xodim ishxonada deb qoladi |
| **Sabab** | Sabab ro'yxati chiqadi (5.7) |

#### 5.6.4 Zaxira yopilish

```
   Ketganingizni belgilamadingiz.
   Oxirgi ko'rinish bo'yicha 18:05 deb yozdik.

   [ To'g'ri ]   [ Tuzatish ]
```

#### 5.6.5 Javob berilmasa

**Yozuv saqlanib qoladi.** Hisobotda "sababi belgilanmagan" deb turadi. Tizim javob kutib turmaydi va hech narsa to'xtamaydi.

### 5.7 Sabab

"Sabab" bosilganda:

```
   Sababi?

   [ Obed ]            [ Shifokor ]
   [ Shaxsiy ish ]     [ Ish topshirig'i ]
   [ Boshqa ]
```

`Boshqa` → xodim matn yozib yuboradi.

Tasdiq:

```
✓ Yozildi · 15:20 · Shifokor
```

#### 5.7.1 Qaytish — tizim o'zi aniqlaydi

Sabab belgilangandan keyin **"qaytasizmi?" deb so'ralmaydi**:

- Xodim qaytib kirsa → kamera ko'radi → **tanaffus o'zi yopiladi**
- Qaytmasa → kun shu yerda tugaydi

Sabab shunchaki **yorliq**. Qolganini kamera hal qiladi.

**Shuning uchun obeddan qaytganda turish shart emas** — shunchaki kirib keladi. Xodimga kuniga **ikki marta** turish kifoya: ertalab va ketishda.

### 5.8 Xona kamerasi

Davomatga **yozmaydi**. Uchta vazifasi:

#### 5.8.1 Ish joyida borligi — *ishonchli*

Xona kadrida har bir ish joyi uchun **zona** belgilanadi, admin panelda zona xodimga biriktiriladi.

Kim ekanini yuzdan emas, **o'rindiqdan** aniqlaydi. 4 metrdan yuz tanish zaif, "3-stol = Ali" esa xato qilmaydi. Bu devordagi poster muammosini ham yopadi — poster ish joyi zonasida turmaydi va harakatlanmaydi.

#### 5.8.2 Davomatni tekshirish — *ishonchli*

Tizim "Ali ketdi" deb yozsa, lekin xona kamerasi Alini ish joyida ko'rishda davom etsa → yozuv **shubhali** deb belgilanadi va admin ko'radi. Jimgina xato ketmaydi.

#### 5.8.3 Faoliyat holati — *tajriba bosqichi*

Tana holati bo'yicha (bosh, yelka, bilak nuqtalari), **telefonni qidirib emas**:

| Holat | Belgilari |
|---|---|
| Ishlayapti | Bosh monitorga qaragan, bilaklar stol sathida |
| Telefonda | Bosh pastga egilgan, bilaklar ko'tarilgan |
| Joyida yo'q | Zonada odam yo'q |

**Qoidalar:**

- `3 daqiqadan` uzun holatlar hisobga olinadi, qisqasi **shovqin deb tashlanadi**
- Natija **aniq raqam sifatida emas**, uzun oyna bo'yicha yig'ilgan ko'rsatkich sifatida beriladi
- Ishonch darajasi har bir yozuvda saqlanadi

> **Ochiq ogohlantirish:** telefon aniqlash bir marta sinab ko'rilgan va ishlamagan (sichqoncha telefon deb, devordagi poster odam deb belgilangan). 4 metrdan telefon ~20×40 piksel bo'ladi — bu ishonchli aniqlanmaydi. Shuning uchun yondashuv o'zgartirildi: tana holati o'lchanadi. Buning ishlashi **haqiqiy kameradan 2–3 kunlik yozuv olib tekshirilgandan keyin** tasdiqlanadi. Oldindan kafolat berilmaydi.

### 5.9 Dashboard

| Bo'lim | Tarkibi |
|---|---|
| Kun jadvali | Har bir xodim uchun alohida yo'l, soat o'qi, mavjudlik bandlari, "hozir" chizig'i |
| Kelish/ketish | Vaqt bo'yicha ro'yxat, kechikish ajratilgan |
| Kamera | Jonli tasvir (MJPEG), tanilgan yuzlar belgilangan |
| Hozir ishxonada | Xodimlar ro'yxati va kelgan vaqti |
| Shubhali yozuvlar | Xona kamerasi tasdiqlamagan yozuvlar |

### 5.10 Hisobotlar

| Hisobot | Tarkibi |
|---|---|
| Kunlik | Kelish/ketish vaqti, jami soat, tanaffuslar, kechikish |
| Oylik | Xodim bo'yicha jami soat, kechikish soni, erta ketish soni |
| Kechikishlar | Saralangan ro'yxat, sabablari bilan |
| Sababsiz erta ketishlar | Tugma bosilmagan yoki "Ha" bosilgan holatlar |
| Eksport | CSV / Excel |

### 5.11 Admin panel

| Imkoniyat | Holati |
|---|---|
| Xodim qo'shish + yuz skanerlash | Bajarilgan |
| Ism/familiya/lavozim tahrirlash | Bajarilgan |
| Qayta skanerlash (namuna qo'shish) | Bajarilgan |
| Xodimni o'chirish | Bajarilgan |
| Takroriy yuz nazorati | Bajarilgan |
| **Telegram kodi berish** | Qilinishi kerak |
| **Bog'lanishni uzish** | Qilinishi kerak |
| **Davomatni qo'lda tuzatish** | Qilinishi kerak |
| **Ish joyi zonalarini belgilash** | Qilinishi kerak |

**Qo'lda tuzatish zarur** — xodim esdan chiqaradi, kamera xato qiladi, elektr o'chadi. Har bir tuzatish **kim va qachon** qilgani bilan saqlanadi.

---

## 6. Ma'lumotlar bazasi

SQLite, WAL rejimida (bir necha jarayon bir vaqtda yozadi).

```sql
-- Xodimlar
employees (
  id            INTEGER PRIMARY KEY,
  first_name    TEXT NOT NULL,
  last_name     TEXT DEFAULT '',
  position      TEXT DEFAULT '',
  created_at    TEXT NOT NULL
)
-- UNIQUE(name) yo'q: bir xil ismli ikki xodim bo'lishi mumkin

-- Yuz namunalari
face_embeddings (
  id            INTEGER PRIMARY KEY,
  employee_id   INTEGER NOT NULL,
  embedding     BLOB NOT NULL,      -- 512 x float32
  source_image  TEXT,
  created_at    TEXT NOT NULL
)

-- Davomat hodisalari
attendance_events (
  id            INTEGER PRIMARY KEY,
  employee_id   INTEGER NOT NULL,
  ts            TEXT NOT NULL,      -- kamera ko'rgan payt
  event         TEXT NOT NULL,      -- 'keldi' | 'ketdi'
  source        TEXT NOT NULL,      -- 'deliberate' | 'first_sighting'
                                    -- | 'auto_close' | 'manual'
  confirmed     TEXT,               -- NULL | 'ha' | 'yoq' | 'sabab'
  reason        TEXT,               -- 'obed' | 'shifokor' | ... | matn
  confirmed_at  TEXT,
  cancelled     INTEGER DEFAULT 0,
  suspicious    INTEGER DEFAULT 0,  -- xona kamerasi tasdiqlamadi
  note          TEXT
)

-- Ko'rinishlar (jadval chizish uchun, har 15 sekundda)
presence_samples (
  id            INTEGER PRIMARY KEY,
  employee_id   INTEGER NOT NULL,
  ts            TEXT NOT NULL,
  camera        TEXT NOT NULL       -- 'entrance' | 'room'
)

-- Telegram bog'lanishlari
telegram_links (
  employee_id   INTEGER PRIMARY KEY,
  telegram_id   INTEGER UNIQUE NOT NULL,
  linked_at     TEXT NOT NULL
)

-- Bog'lanish kodlari
pairing_codes (
  code          TEXT PRIMARY KEY,
  employee_id   INTEGER NOT NULL,
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  used_at       TEXT
)

-- Ish joyi zonalari
desk_zones (
  id            INTEGER PRIMARY KEY,
  employee_id   INTEGER,
  x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
  label         TEXT
)

-- Faoliyat holatlari
activity_states (
  id            INTEGER PRIMARY KEY,
  employee_id   INTEGER NOT NULL,
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  state         TEXT NOT NULL,      -- 'ish' | 'telefon' | 'yoq'
  confidence    REAL
)
```

---

## 7. Tanish parametrlari

| Parametr | Qiymat | Izoh |
|---|---|---|
| Model | InsightFace `buffalo_l` | SCRFD aniqlash + ArcFace 512-o'lcham |
| Aniqlash o'lchami | 640 × 640 | |
| Moslik chegarasi | `0.40` kosinus | Bir xil odam `> 0.5`, boshqa odam `< 0.3` |
| Takroriylik chegarasi | `0.93` | Namuna qo'shishda |
| Minimal `det_score` | `0.60` | Xira kadr saqlanmaydi |
| Minimal yuz kengligi | `70 px` (skanerlash) / `180 px` (davomat) | |
| Tanish tezligi | 2/sek (davomat), 1–2/sek (xona) | |
| GPU | CUDAExecutionProvider | 49 ms/kadr o'lchangan |

> `onnxruntime` (CPU) va `onnxruntime-gpu` birga o'rnatilmasligi kerak — CPU versiyasi GPU'ni berkitadi.

---

## 8. Xavfsizlik va ruxsatlar

### 8.1 Ikki darajali himoya

| Daraja | Nimaga ruxsat | Himoya |
|---|---|---|
| **Ko'ruvchi** | Dashboard, hisobotlar | HTTP Basic parol |
| **Boshqaruvchi** | Xodim qo'shish/o'chirish, kod berish, tuzatish | **Alohida parol** + sessiya cookie (8 soat) |

Boshqaruv paroli ko'ruvchi parolidan **alohida**. Dashboardni ko'rish huquqi xodim qo'shish huquqini bermaydi.

### 8.2 Qoidalar

- Parollar va tokenlar hech qachon ekranga chiqarilmaydi va logga yozilmaydi
- Telegram bot tokeni muhit o'zgaruvchisida (`.env`), kodda emas
- **Kompaniya boti shaxsiy botdan alohida** — alohida token, alohida jarayon
- Barcha boshqaruv amallari jurnalga yoziladi: kim, qachon, nima
- Tizim faqat lokal tarmoqda ishlaydi, internetdan ochiq emas

### 8.3 Maxfiylik

| Talab | Yechim |
|---|---|
| Xodim roziligi | Yozma rozilik olinadi (olingan) |
| Xodim o'z ma'lumotini ko'rishi | `/bugun`, `/hafta` buyruqlari |
| Video arxiv | Saqlanmaydi. Faqat davomat yozuvlari va yuz namunalari |
| Yuz namunalari | Embedding — matematik tavsif, rasmga qaytarilmaydi |
| Saqlash muddati | **Kelishilishi kerak** (tavsiya: 90 kun, keyin avtomatik o'chadi) |
| Faoliyat ma'lumoti | Rahbariyatga umumiy ko'rsatkich, xodimga o'z ma'lumoti |

---

## 9. Nofunksional talablar

| Talab | Qiymat |
|---|---|
| Kamera tasviri | Realtime, kechikish `< 0.5 sek` |
| Davomat yozilishi | Turish tugagandan `< 2 sek` keyin |
| Telegram xabari | `< 10 sek` ichida yetib borishi |
| Ishlash muddati | 24/7, avtomatik qayta ishga tushish (`systemd`) |
| Elektr o'chganda | Yoqilgandan keyin o'zi ishga tushadi, tugallanmagan sessiyalar yopiladi |
| Baza | Bir vaqtda bir necha jarayon yozishi (WAL + `busy_timeout`) |
| Zaxira nusxa | Kunlik avtomatik baza nusxasi |

---

## 10. Bosqichlar

### 1-bosqich — Davomat mantig'i *(Telegram'siz)*

- Ataylab turishni aniqlash
- Keldi / ketdi yozish
- Vaqt qoidalari (09:00 / 18:00)
- Zaxira yo'llar
- Dashboardda ko'rinishi

**Qabul mezoni:**

| Sinov | Talab |
|---|---|
| Kelib 3 sekund turish | 20 urinishdan `≥ 19` yozilishi |
| Yonidan o'tib ketish | 50 o'tishdan `0` yozilishi |
| Boshqa odamni o'zi deb yozishi | 100 urinishdan `0` |

### 2-bosqich — Telegram

- Alohida bot
- Bog'lanish kodi (admin panel + bot)
- Keldi / ketdi xabarlari
- Tugmalar: Ha / Yo'q / Sabab
- Sabab ro'yxati
- `/bugun`, `/hafta`

**Qabul mezoni:** 3 xodim bog'lanadi, har biri xabar oladi, tugma bosadi, natija bazada to'g'ri ko'rinadi.

### 3-bosqich — Haqiqiy kameralar

- RTSP ulanish
- Kirish kamerasini o'rnatish va sozlash
- **Barcha xodimlarni haqiqiy kameradan qayta skanerlash**
- Masofa va sekund parametrlarini sozlash

> Qayta skanerlash aniqlikni eng ko'p oshiradigan qadam. Laptop kamerasidan 50 sm dan olingan namuna, kirish kamerasidan 1.5 m dan ko'rinadigan yuzga to'liq mos kelmaydi.

### 4-bosqich — Xona kamerasi

- Ish joyi zonalari
- "Joyida bor / yo'q"
- Davomatni tekshirish

### 5-bosqich — Faoliyat tahlili *(tajriba)*

- 2–3 kunlik yozuv yig'ish
- Tana holati modelini shu yozuvda tekshirish
- **Natijaga qarab qaror:** ishlatiladimi yoki yo'q

---

## 11. Hal qilinishi kerak bo'lgan savollar

Loyihani boshlashdan oldin kompaniyadan aniqlash kerak:

| № | Savol | Nega muhim |
|---|---|---|
| 1 | Rasmiy ish vaqti `09:00–18:00` to'g'rimi? | Kechikish shunga qarab hisoblanadi |
| 2 | Smenalar bormi? | Smena bo'lsa vaqt qoidalari xodimga bog'lanadi |
| 3 | Shanba ish kunimi? | Hisobotda ish kunlari hisobi |
| 4 | Kamera modeli va RTSP manzili | Ulanish uchun |
| 5 | Server qayerda turadi? | Tarmoq va o'rnatish |
| 6 | Kirish kamerasi **1.5–1.7 m** ga o'rnatiladimi? | Tanish aniqligi hal qiluvchi darajada bog'liq |
| 7 | Ma'lumot qancha saqlanadi? | Tavsiya: 90 kun |
| 8 | Kim boshqaruv paroliga ega bo'ladi? | Xodim qo'shish huquqi |
| 9 | Xonada nechta ish joyi va joylashuvi o'zgaradimi? | Zonalar |
| 10 | Faoliyat (telefon) hisoboti **qat'iy talabmi**? | Taxminiy ko'rsatkich bo'lishiga rozilik kerak |
| 11 | Telegram'i yo'q xodim bormi? | Zaxira yo'l kerak bo'ladi |

---

## 12. Hozirgi holat

### Bajarilgan

**Yuz tanish**
- InsightFace + GPU yadrosi (49 ms/kadr)
- Burchak bo'yicha ro'yxatga olish (8 namuna, 5 burchak) + sifat nazorati
- Takroriy yuz nazorati

**Davomat (1-bosqich)**
- Ataylab turishni aniqlash: yaqin + qimirlamaydi + 3 sekund + ovoz berish
- Chegara kadr kengligiga nisbatan (kameralar orasida ko'chadi) + piksel pastki chegarasi
- Keldi / ketdi qarori, takroriy turish nazorati
- Vaqt qoidalari: kechikish, erta ketish, sabab kerakligi (soat + daqiqa)
- Zaxira yo'llar: turmagan odam uchun `first_sighting`, ko'rinmay qolgan kun uchun `auto_close`
- 31 ta avtomatik test

**Interfeys va infratuzilma**
- Admin panel: qo'shish, tahrirlash, qayta skanerlash, o'chirish
- Ikki darajali parol himoyasi
- Dashboard: kun jadvali, jonli kamera, kelish/ketish, kun hisobi
- Realtime kamera (MJPEG, atomik yozish), ekranda piksel o'lchovi
- Ko'p jarayonli baza (WAL)
- `data/config.json` — barcha qiymatlar sozlanadigan
- `scripts/today.py` — testni qaytadan boshlash

**Telegram (2-bosqich)**
- Bog'lanish kodi: 8 belgi, bir martalik, 24 soat, urinish cheklovi bilan
- Keldi / ketdi xabarlari (soat + daqiqa bilan)
- Erta ketishda `Ha / Yo'q / Sabab` tugmalari va sabab ro'yxati
- `/bugun`, `/hafta` — xodim o'z ma'lumotini ko'radi
- Navbat (`telegram_outbox`): xabarlar yuborilmasa ham yoziladi va ko'rinadi
- Ikki alohida kalit: bot ishlashi va xabar yuborish

**Qo'lda tuzatish (2-bosqich)**
- Yozuv qo'shish, vaqtini/turini tuzatish, bekor qilish
- Tuzatilgan yozuv `manual` deb belgilanadi, bekor qilingani o'chirilmaydi

**Hisobotlar (2-bosqich)**
- Oylik: jami soat, kechikkan kun, kechikish daqiqasi, erta ketish,
  sababsiz erta ketish, qo'lda tuzatilgan yozuvlar
- CSV eksport (Excel uchun BOM bilan)

Jami **75 ta avtomatik test**, hammasi o'tadi.

### Qilinishi kerak

| № | Ish | Bosqich |
|---|---|---|
| 1 | Haqiqiy kameraga ulanish (RTSP), joyida sozlash | 3 |
| 2 | Xodimlarni haqiqiy kameradan qayta skanerlash | 3 |
| 3 | Serverda `systemd` xizmati + kunlik zaxira nusxa | 3 |
| 4 | Xona kamerasi: ish joyi zonalari, "joyida bor/yo'q" | 4 |
| 5 | Davomatni xona kamerasi bilan tekshirish (shubhali yozuvlar) | 4 |
| 6 | Faoliyat tahlili (tana holati) — tajriba | 5 |

**Dasturiy ishning kamerasiz qilinadigan qismi tugadi.** Qolgan hammasi
kameralar o'rnatilishini kutadi.

> `systemd` xizmati yozilgan va sinalgan (qulasa ko'tariladi, qayta yonsa
> ishga tushadi), lekin hozircha **o'chirilgan** — lokal test rejimida
> qo'lda ishga tushiriladi. Serverga o'tganda qaytariladi.

---

## 13. Eslatma

Telegram xabarlari **o'chirilgan holatda** ishlab chiqiladi va buyurtmachining aniq ruxsatidan keyin yoqiladi.
