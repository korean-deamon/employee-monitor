# employee-monitor

Kamera orqali xodimlar davomati. To'liq texnik topshiriq: [TZ.md](TZ.md).

Tizim **24/7 ishlaydi** — kamera hech qachon o'chmaydi. Soat davomatni aniqlamaydi,
faqat baholaydi (kechikish, erta ketish).

## O'rnatish

```bash
git clone <repo> employee-monitor
cd employee-monitor
./setup.sh                 # GPU (NVIDIA, CUDA 12.8)
./setup.sh --cpu           # GPU yo'q bo'lsa — ishlaydi, lekin ~10 barobar sekin
```

Script venv yaratadi, kutubxonalarni o'rnatadi va tana modellarini yuklab
oladi. Qayta ishga tushirsa bo'ladi — bajarilgan qadamlarni o'tkazib yuboradi.

Keyin:

```bash
cp .env.example .env       # Telegram kerak bo'lsa, tokenni yozing
chmod 600 .env
./emon start
```

Parollar birinchi ishga tushirishda **o'zi yaratiladi** —
`data/admin_pass.txt` va `data/manage_pass.txt`.

### Bilib qo'yish kerak bo'lgan ikki narsa

**Yuz modeli (~326 MB)** birinchi ishga tushirishda internetdan yuklanadi va
`~/.insightface/` ga saqlanadi. Shundan keyin internet kerak emas. Internetsiz
serverga o'rnatayotgan bo'lsangiz, bu papkani qo'lda ko'chiring.

**PyTorch har kompyuterda bir xil o'rnatilmaydi.** `setup.sh` CUDA 12.8 uchun
yozilgan. Boshqa drayver bo'lsa, PyTorch saytidagi mos buyruqni ishlating yoki
`--cpu` bilan o'rnating. `requirements.lock.txt` aynan shu mashinaning nusxasi —
boshqa drayverda ishlamaydi, faqat shu muhitni qayta tiklash uchun.

## Ishga tushirish

Bitta script hammasini boshqaradi:

```bash
cd ~/Projects/employee-monitor
./emon start      # ishga tushirish
./emon stop       # to'xtatish
./emon restart    # qayta ishga tushirish (sozlama o'zgargandan keyin)
./emon status     # nima ishlayapti, kamera qanday ko'rayapti
./emon logs       # kamera logi (logs api, logs bot ham bor)
```

`start` uchta jarayonni ko'taradi: API, kamera va — `telegram_enabled` yoqilgan
bo'lsa — Telegram boti. O'chirilgan bo'lsa botni ishga tushirmaydi va shuni
aytib qo'yadi, ya'ni Telegramni yoqish-o'chirish **sozlama** o'zgarishi, boshqa
buyruq esdan chiqarish emas.

`start` modelni yuklash uchun 25 sekund kutadi, keyin holatni ko'rsatadi —
shoshib tekshirilsa kamera hali ochilmagan bo'lib "ishlamadi" deb ko'rinardi.

`stop` avval SIGTERM yuboradi, shunda kamera qurilmani bo'shatadi; javob
bermagan jarayongagina SIGKILL tegadi. **Hech kim "ketdi" deb belgilanmaydi** —
kameraning o'chishi odamlarning ketishi emas; ochiq kunlar keyingi ishga
tushirishda haqiqiy oxirgi ko'rinish vaqti bilan yopiladi.

Serverga o'rnatilganda bular `systemd` xizmatiga o'tadi, shunda kompyuter
yonganda o'zi ishga tushadi va qulasa o'zi ko'tariladi.

## Sahifalar

| Manzil | Nima |
|---|---|
| `/` | Dashboard: kun jadvali, jonli kamera, kelish/ketish, kun hisobi |
| `/admin` | Xodim qo'shish, yuz skanerlash, davomatni tuzatish, Telegram |
| `/counter` | Eshikdan kirdi/chiqdi hisobi — **alohida sahifa** |

Dashboard paroli `data/admin_pass.txt`, boshqaruv paroli `data/manage_pass.txt`
faylida (foydalanuvchi nomi `admin`). Boshqaruv paroli alohida: dashboardni
ko'rish huquqi xodim qo'shish huquqini bermaydi.

## Davomat qanday yoziladi

**Keldi** — xodim kameraga yaqin kelib 3 sekund turadi. Turishni esdan
chiqarsa, bugungi birinchi ko'rinishi `first_sighting` sifatida yoziladi, ya'ni
kelgan vaqti yo'qolmaydi.

**Ketdi** — ketishdan oldin yana kelib turadi. Esdan chiqarsa, 30 daqiqa
ko'rinmagandan keyin tizim kunni **oxirgi ko'rinish vaqti bilan** o'zi yopadi.

Yonidan o'tib ketgan odam yozilmaydi: turish uchun yuz yetarlicha katta
bo'lishi, qimirlamasligi va 3 sekund turishi kerak — uchalasi birga.

Kamera to'xtaganda hech kim "ketdi" bo'lmaydi. Kameraning o'chishi odamlarning
ketishi emas; ochiq kunlar keyingi ishga tushishda yopiladi.

## Sozlash

`data/config.json` — barcha qiymatlar shu yerda. O'zgartirgandan keyin
`pkill -f recognizer.py` va qayta ishga tushiring:

```json
{
  "timezone": "Asia/Tashkent",
  "work_start": "09:00",
  "work_end": "18:00",
  "stand_min_face_ratio": 0.18,
  "stand_min_face_px": 100,
  "stand_seconds": 3.0,
  "min_session_seconds": 600,
  "absence_close_seconds": 1800
}
```

| Qiymat | Ma'nosi |
|---|---|
| `stand_min_face_ratio` | Yuz kadr kengligining qancha qismini egallashi kerak — "yaqin keldimi" |
| `stand_min_face_px` | Yuzning minimal piksel kengligi — "tanish uchun detal yetarlimi" |
| `stand_seconds` | Qancha turish kerak |
| `min_session_seconds` | Kelgandan keyin shu vaqt ichidagi turish takroriy deb o'tkaziladi |
| `absence_close_seconds` | Shu vaqt ko'rinmasa kun o'zi yopiladi |
| `timezone` | Ofis vaqt mintaqasi |
| `telegram_enabled` | Bot umuman ishlaydimi |
| `telegram_send_notifications` | Xabarlar rostdan yuboriladimi |
| `body_mode` | Tana aniqlash: `off` · `box` · `seg` · `pose` · `full` |
| `body_fps` | Tana sekundiga necha marta tekshiriladi |

### Tana aniqlash

Yuz modeli (InsightFace) faqat kameraga qaragan yuzni ko'radi. Tana uchun
alohida model ishlatiladi (YOLOv8), chunki eshikdan o'tayotgan odam yuzini
burgan bo'ladi.

| Rejim | Nima chizadi | Qachon kerak |
|---|---|---|
| `off` | hech narsa | eng tez |
| `box` | to'rtburchak ramka | **eshikdan o'tganlarni sanash** uchun yetarli |
| `seg` | tana shakli bo'ylab kontur | modelning ishini ko'rish uchun |
| `pose` | skelet: yelka, tirsak, bilak, son | **ish/telefon holati** uchun |
| `full` | kontur + skelet birga | ikki model ishlaydi, ikki barobar og'ir |

Skelet **bilakda tugaydi** — barmoq yo'q. Barmoq uchun boshqa model kerak
bo'lardi, lekin 2 metrdan butun qo'l ~50 piksel, barmoq ~10 — rasmda yo'q.
Kerak ham emas: telefon ushlagan qo'lni bilak balandligi, boshning egilishini
esa burun–yelka chizig'i ko'rsatadi.

Bosh nuqtalaridan faqat **burun** chiziladi. Ko'z va quloq bu masofada bir
necha piksel, ishonchsiz va hech qanday savolga javob bermaydi.

> Devordagi odam tasviri bor posterlar ham tana deb aniqlanadi. Eshikdan
> sanashga o'tilganda bu o'zi hal bo'ladi — poster chiziqni kesmaydi va
> qimirlamaydi. Hozircha "tana: N" raqami haqiqiy odam soni emas.

Yuz **ikkala shartdan ham** o'tishi kerak. Ikkitasi bo'lgani bejiz emas:
nisbat "odam kameraga yaqin keldimi" degan savolga javob beradi va kamera
ruxsati o'zgarsa ham ishlaydi; piksel chegarasi esa "tanish uchun detal
yetarlimi" degan boshqa savolga javob beradi va u modelning talabi.

Ikkisi ham **har bir kamera uchun joyida sozlanadi** — linza burchagi va
masofa boshqacha bo'ladi. Kamera tasvirida har bir yuz yonida piksel kengligi
yoziladi (`210px`, yoki `140px uzoq`), chegara shu o'lchovga qarab tanlanadi.

## Test

```bash
venv/bin/python scripts/today.py            # bugungi yozuvlar
venv/bin/python scripts/today.py --clear    # testni qaytadan boshlash
```

`--clear` faqat bugungi davomat yozuvlarini o'chiradi; xodimlar va yuz
namunalari saqlanadi.

Avtomatik testlar:

```bash
./tests/run.sh
```

Testlar vaqtinchalik bazada ishlaydi — `data/activity.db` ga hech qachon
tegmaydi, shuning uchun ishlab turgan tizimda ham xavfsiz.


## Telegram

Ikkita alohida kalit bor, va ular **boshqa-boshqa vaqtda** yoqiladi:

| Kalit | Yoqilganda nima bo'ladi |
|---|---|
| `telegram_enabled` | Bot ishga tushadi: bog'lanish, `/bugun`, `/hafta`, tugmalar ishlaydi |
| `telegram_send_notifications` | Keldi/ketdi xabarlari rostdan xodimlarga boradi |

Ikkisi ham **o'chirilgan** holatda keladi. Xabarlar har holda tayyorlanib
**navbatga** (`telegram_outbox`) yoziladi va admin sahifasida ko'rinadi — ya'ni
nima yuborilishini xodimlarning telefoni jiringlashidan oldin ko'rib olasiz.

Birinchi kalitni yoqib, ikkinchisini o'chiq qoldirish — sinab ko'rish uchun eng
qulay holat: bog'lanishni tekshirasiz, xabarlar esa chiqmaydi.

### Xodimni ulash

1. Admin sahifasida xodim qatorida **Kod olish** bosiladi — `K7M4-RPQX` chiqadi
2. Kod xodimga beriladi (og'zaki yoki qog'ozda)
3. Xodim botni ochadi, `/start` bosadi va kodni yuboradi
4. Admin sahifasida **ulangan** deb ko'rinadi

Kod **bir marta** ishlaydi, **24 soat** amal qiladi, va yangi kod berilganda
eskisi bekor bo'ladi. Noto'g'ri urinish soatda 5 ta bilan cheklangan.

### Bot buyruqlari

| Buyruq | Nima |
|---|---|
| `/start`, `/help` | Holat va buyruqlar |
| `/bugun` | Bugungi yozuvi, kechikishi bilan |
| `/hafta` | Shu haftaning hisobi, har kun kechikishi bilan |
| `/kechikish` | Oxirgi 30 kundagi har bir kechikish va jami |

### Erta ketish savoli

18:00 dan oldin ketgan xodimga tugmali xabar boradi: **Ha · Yo'q · Sabab**.
`Yo'q` yozuvni bekor qiladi (xodim ishxonada deb qoladi), `Sabab` esa ro'yxat
chiqaradi: Obed, Shifokor, Shaxsiy ish, Ish topshirig'i, Boshqa.

Tugma bosilmasa yozuv **saqlanib qoladi** — hisobotda "sababsiz erta ketish"
deb turadi. Tizim javob kutib turmaydi.

## Hisobotlar

| Manzil | Nima |
|---|---|
| `/api/summary` | Bugungi kun: har bir xodim, soat, tanaffus |
| `/api/report/monthly` | Oylik: jami soat, kechikish, erta ketishlar |
| `/api/report/late` | Kechikishlar: har biri alohida + xodim bo'yicha jami |
| `/api/report/monthly.csv` | Oylik hisobot Excel uchun fayl |

Oylik hisobotga `?month=2026-10`, kechikishlarga `?days=30` berib davrni
o'zgartirish mumkin.

Kechikish dashboardda alohida bo'lim bo'lib turadi: bugun kech qolganlar yirik
shriftda, ostida xodim bo'yicha jadval (nechta kun, jami, o'rtacha, eng ko'p),
va ochiladigan ro'yxatda har bir kechikish alohida. Har joyda soat va daqiqa
bilan — `3 soat 47 daqiqa kech`, `8 daqiqa kech`.

## Qo'lda tuzatish

Kamera xato qilsa yoki xodim turishni esdan chiqarsa, yozuv qo'lda tuzatiladi.
Har bir tuzatilgan yozuv `source = manual` deb belgilanadi va hisobotda alohida
ustunda ko'rinadi — ya'ni kameraning ishi bilan qo'lning ishi aralashmaydi.

| Amal | So'rov |
|---|---|
| Vaqtini/turini tuzatish | `PATCH /api/admin/events/{id}` |
| Yangi yozuv qo'shish | `POST /api/admin/events` |
| Yozuvni bekor qilish | `DELETE /api/admin/events/{id}` |

Bekor qilingan yozuv **o'chirilmaydi** — `cancelled = 1` deb belgilanadi, ya'ni
kamera nimani ko'rgani va kim bekor qilgani javobgar qoladi.


## Zaxira nusxa

Baza yo'qolsa **hamma yuz namunalari va davomat tarixi** ketadi, va ularni
hech narsadan tiklab bo'lmaydi. Shuning uchun nusxa avtomatik olinadi.

`./emon start` har safar ishga tushganda nusxa oladi — baza aynan o'sha paytda
bo'sh turadi, ya'ni eng xavfsiz payt.

```bash
venv/bin/python scripts/backup.py            # qo'lda nusxa olish
venv/bin/python scripts/backup.py --list     # nusxalarni ko'rish
venv/bin/python scripts/backup.py --keep 60  # 60 kun saqlash (standart 30)
```

Nusxalar: `data/backups/activity_YYYY-MM-DD_HHMM.db`, 30 kundan eskilari
o'zi o'chiriladi.

Nusxa SQLite'ning o'z `backup` mexanizmi bilan olinadi, oddiy fayl nusxasi
bilan emas — yozilayotgan bazani oddiy ko'chirish yarim tugagan tranzaksiyani
ushlab qolishi va buzuq fayl berishi mumkin.

**Tiklash:** `data/backups/` dan kerakli faylni `data/activity.db` ustiga
ko'chirasiz (avval `./emon stop`).

## Davomatni tuzatish (admin panel)

`/admin` sahifasining **"Davomatni tuzatish"** bo'limida:

| Amal | Qanday |
|---|---|
| Kunni tanlash | Yuqoridagi sana maydoni |
| Vaqtini to'g'irlash | Qator yonidagi **Vaqtini tuzatish** |
| Yozuvni bekor qilish | **Bekor qilish** — o'chirilmaydi, chizib qo'yiladi |
| Bekor qilinganni qaytarish | **Qaytarish** |
| Yangi yozuv qo'shish | **Yozuv qo'shish** — xodim, kirdi/chiqdi, vaqt |

Tuzatilgan yozuv `manual` deb belgilanadi va oylik hisobotda alohida ustunda
ko'rinadi — kameraning ishi bilan qo'lning ishi aralashmaydi.

Bekor qilingan yozuv **ro'yxatda qoladi**, chizib qo'yilgan holda. Shuning
uchun noto'g'ri bekor qilish ko'rinadi va qaytariladi — ma'lumot yo'qolgandek
tuyulmaydi.

## Eshikdan sanash (counter)

**Davomatdan butunlay alohida.** Davomat "bu kim" degan savolga javob beradi —
yuzdan, va faqat odam kameraga kelib turganda. Sanash esa "nechta odam o'tdi"
degan boshqa savolga javob beradi — tanadan, va **hamma uchun**: mehmon ham,
o'quvchi ham, ro'yxatda bo'lmagan odam ham.

Ikkisi hech qachon bir xil raqam bermaydi va berishi ham shart emas.

| Sozlama | Ma'nosi |
|---|---|
| `counter_enabled` | Sanash yoqilganmi (standart: `false`) |
| `door_line` | Chiziq: `[[x1,y1],[x2,y2]]`, kadr o'lchamiga nisbatan |
| `door_invert` | Yo'nalishni almashtirish (kirdi ↔ chiqdi) |
| **`count_point`** | **Tananing qaysi nuqtasi chiziqni kesishi kerak** |
| `count_cooldown_seconds` | Chiziq ustida turgan odam qayta sanalmasligi uchun |

### `count_point` — qaysi nuqta sanaladi

Butun tana emas, skelet ham emas — **bitta nuqta**. Qaysi nuqta ekani kameraga
bog'liq, kodga emas:

| Qiymat | Qayerda | Qachon to'g'ri |
|---|---|---|
| `foot` | Ramkaning pastki o'rtasi | **Kamera odamni to'liq ko'rsa.** Oyoq polda turgan joy — odam chiziqdan haqiqatan o'tgan lahza shu |
| `nose` | Burun (pose rejimi kerak) | **Kamera faqat belidan yuqorisini ko'rsa.** Unda ramka pasti kadr chetida qotib qoladi va hech qachon chiziqni kesmaydi |
| `centre` | Ramka markazi | Oraliq variant, kamdan-kam kerak |

**Hozir `nose` qo'yilgan** — laptop kamerasida tana pastdan kesilgani uchun.
O'lchab ko'rilgan: tana `y=82..479`, kadr balandligi `480` — ya'ni oyoq
nuqtasi kadr chetiga yopishib qolgan va qimirlamaydi.

**Haqiqiy kamera o'rnatilgandan keyin `foot` ga qaytaring:**

```json
"count_point": "foot"
```

`data/config.json` da shu bitta satrni o'zgartirib, `./emon restart` deysiz.

Nega qaytarish kerak: shiftga o'rnatilgan kamera odamni to'liq ko'radi, va
o'shanda burun chiziqni **oyoqdan oldinroq** kesadi — odam hali eshikdan
o'tmasdan "o'tdi" deb yozilib qolishi mumkin. Oyoq nuqtasi esa haqiqiy
o'tish lahzasini beradi.

Agar tanlangan nuqta topilmasa (pose o'chiq, yoki burun aniq ko'rinmasa),
tizim o'zi **oyoq nuqtasiga qaytadi** — sanash to'xtab qolmaydi.

### Chiziq yo'nalishi

| Chiziq | `door_line` | Qachon |
|---|---|---|
| **Gorizontal** (chapdan o'ngga) | `[[0, 0.6], [1, 0.6]]` | Eshik uchun — odam yuqoridan pastga yoki aksincha o'tadi |
| Vertikal | `[[0.5, 0], [0.5, 1]]` | Odam yonma-yon o'tadigan joy uchun |

Hozir **gorizontal** qo'yilgan, kadrning 60% balandligida. Chiziq `/counter`
sahifasida ko'rinib turadi — raqamni o'zgartirasiz, chiziq suriladi.

Chiziq `/counter` sahifasida **ko'rinib turadi** — raqamni o'zgartirasiz,
chiziq suriladi. Masalan `[[0, 0.6], [1, 0.6]]` — kadrning 60% balandligida
gorizontal chiziq.

**Ajratilganligi:** counter o'z moduli (`counting.py`), o'z jadvali
(`crossings`), o'z rasmi (`counter_snapshot.jpg`) va o'z sahifasida. Kamera
siklidagi chaqiruvi `try/except` ichida — counter qulasa logda yoziladi va
**davomat ishlashda davom etadi**.

Butunlay o'chirish: `"counter_enabled": false` → `./emon restart`.
