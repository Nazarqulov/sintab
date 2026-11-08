# Samsara Fleet Telegram Bot

Samsara alertlarini Telegram guruhlari va xodimlarga yetkazadigan, ularni qabul qilish/yopish,
hisobot va boshqaruvni bitta botda jamlaydigan tizim. Til: o'zbek, rus, ingliz.
To'liq texnik topshiriq: [TZ.md](TZ.md).

**Holat:** 4-bosqich tayyor — 4.1 (til, menyu), 4.2 (guruhlar, routing, digest, eskalatsiya),
4.3 (ko'rish, hisobotlar, admin, monitoring, backup), 4.4 (logistika: HOS, yoqilg'i, hujjatlar,
eslatmalar, smena xulosasi), 4.5 (ichki chat), 4.6 (Mini App). Keyingisi: 5 — deploy.

## Asosiy buyruqlar

| Buyruq | Kim uchun | Nima qiladi |
|---|---|---|
| `/start`, `/help`, `/menu` | hamma | ro'yxatdan o'tish, yordam, asosiy menyu |
| `/alerts` | view_alerts | alertlar (ochiq/yangi/hammasi), kartadan qabul qilish/yopish |
| `/vehicles`, `/drivers` | view_vehicles | qidiruv, karta, mashinani depoga biriktirish |
| `/where <mashina>` | view_vehicles | jonli joylashuv, tezlik, dvigatel, yoqilg'i (Samsara API) |
| `/hos` | view_vehicles | haydovchilarning qolgan haydash/smena/sikl soatlari (Driver — o'ziniki) |
| `/fuel` | view_vehicles | mashinalar yoqilg'i darajasi, eng kami birinchi |
| `/docs` | hamma (Driver — o'ziniki) | hujjatlar: PDF/Excel/Word/rasm yuklash, yuklab olish, muddat |
| `/chat` | hamma faol foydalanuvchilar | hamkasblar bilan yozishma: matn, fayl, rasm, ovoz, joylashuv |
| `/reports`, `/stats` | view_reports | davr bo'yicha statistika; Excel / PDF / CSV eksport |
| `/settings`, `/profile` | hamma | til, vaqt zonasi, shaxsiy alertlar, jim soatlar |
| `/mute`, `/unmute` | hamma | shaxsiy bildirishnomalarni vaqtincha o'chirish |
| `/admin` | admin huquqlari | foydalanuvchilar, guruhlar, depolar, holat, audit, sozlamalar, backup |
| `/users`, `/groups`, `/depots` | tegishli huquq | boshqaruv; `/groups` — routing qoidasi ustasi |
| `/broadcast` | send_broadcast | istalgan xabar/faylni tanlangan auditoriyaga yuborish |
| `/status` | manage_settings | baza, Samsara API, oxirgi webhook, navbat holati |
| `/bind`, `/unbind` | guruh ichida | guruhni (yoki forum topic'ni) alertlar uchun ulash |

Har foydalanuvchiga Telegram "/" menyusida faqat ruxsati bor buyruqlar ko'rinadi.

## Foydalanuvchilar va huquqlar

- `/start` — ism → telefon (faqat o'z raqami) → lavozim → depo. Telefon Samsara haydovchisiga
  mos kelsa, avtomatik bog'lanadi. So'rov Owner/Admin'larga keladi: tasdiqlash (rol → depolar)
  yoki rad etish (bloklanadi). Tasdiqlanmaguncha foydalanuvchi hech narsani ko'rmaydi.
- Ierarxiya: Owner'ga hech kim tega olmaydi; Admin'ni faqat Owner boshqaradi va tayinlaydi;
  hech kim o'zini o'zgartira olmaydi. Har amal `audit_log`ga yoziladi.
- Ko'rish doirasi: Owner/Admin — hammasi; depo biriktirilgan xodim — o'z depolari; Driver —
  faqat o'z alertlari.
- Anti-spam: 8 ta tezkor so'rov, keyin soniyasiga 1 ta.

Owner botga avval `/start` yozishi kerak — aks holda Telegram unga xabar yetkaza olmaydi.

## Alert oqimi

1. `POST /webhook/samsara` — HMAC imzo + vaqt tamg'asi (5 daqiqa); aks holda `401`.
2. Payload o'qiladi (`AlertIncident` yoki eski `Alert`), turi va darajasi aniqlanadi;
   `eventId` bo'yicha takrorlar tashlanadi; faol mute bo'lsa — saqlanadi, yuborilmaydi.
3. `routing_rules` bo'yicha guruhlar (NULL = har qanday), qoida bo'lmasa — Owner; shaxsiy
   bildirishnoma yoqqanlar (daraja, mute, jim soatlar, ko'rish doirasi hisobga olinadi).
4. Tunda kelgan INFO alertlar guruhlarga ertalab bitta digest bo'lib boradi.
5. Navbat bazada (restart'da yo'qolmaydi), Telegram limitlari va 429 hisobga olinadi.
6. Tugmalar: Qabul qildim → Yopish (sabab tanlanadi), Menga biriktir, Xarita, Mute 1 soat.
   Guruhda alert xabariga reply — izoh. Har chatdagi xabar o'z tilida yangilanadi.
7. N daqiqa qabul qilinmagan critical alert eskalatsiya guruhlari va Owner/Admin'larga ketadi.

Tizim sozlamalari (eskalatsiya, digest oynasi, webhook jimligi...) va alert darajalari
`/admin` → ⚙️ / 🎚 orqali o'zgartiriladi.

## Logistika

- **HOS:** har 10 daqiqada Samsara `/fleet/hos/clocks`; ishlayotgan haydovchida haydash yoki smena
  vaqti belgilangan chegaradan (standart 60 daqiqa) kam qolsa — `hos_warning` alert (kuniga bir marta).
- **Yoqilg'i:** har 30 daqiqada yoqilg'i va GPS yangilanadi; chegaradan (standart 20%) past bo'lsa —
  `low_fuel` alert (mashina uchun kuniga bir marta).
- **Hujjatlar:** fayl Telegram serverida (file_id), bazada — egasi (kompaniya/haydovchi/mashina/alert),
  turi (CDL, medical card, insurance, IFTA, BOL, POD...) va muddat. Muddat 30/14/7/1 kun qolganda va
  o'tganda Owner/Admin'larga hamda tegishli haydovchiga bir martadan eslatma.
- **Smena xulosasi:** har kuni belgilangan soatda (standart 18:00, Chicago) Owner/Admin/Manager'larga
  bugungi alertlar statistikasi.

Chegara va soatlar `/admin` → ⚙️ orqali o'zgartiriladi (0 yoki -1 — o'chirilgan).

## Ichki chat

- `/chat` — kontaktlar (oxirgi suhbatdoshlar yuqorida, qidiruv bor) → suhbat rejimi: yuborilgan har
  xabar (matn, PDF/Excel, rasm, ovoz, joylashuv...) qabul qiluvchiga yuboruvchining ismi va
  "💬 Yozish" tugmasi bilan yetkaziladi. Yetkazilgan xabarga oddiy Telegram reply ham javob bo'ladi.
- Kim kimga yoza oladi: xodimlar o'zaro erkin; haydovchi — o'z deposiga xizmat qiladigan xodimga
  (Viewer'dan tashqari); haydovchilar o'zaro yozishmaydi; bloklangan/tasdiqlanmaganlar — hech kimga.
- Haydovchi kartasida "💬 Yozish" tugmasi bor. Yozishmalar `chat_messages` jadvalida saqlanadi (audit).

## Mini App (web ilova)

Bot ichida ochiladigan ilova: `/app/` (frontend, build bosqichisiz) va `/api/app/*` (API).

- **Bo'limlar (rol bo'yicha):** bosh sahifa, alertlar (qabul qilish, biriktirish, sabab bilan yopish),
  xarita, park (mashinalar, haydovchilar, HOS, yoqilg'i), hujjatlar (ko'rish, yuklash, chatga yuborish),
  hisobotlar (Excel/PDF/CSV chatga keladi), xodimlar (tasdiqlash, rol, depo, bloklash).
- **Xavfsizlik:** har so'rov `Authorization: tma <initData>` bilan keladi; imzo bot token'i bilan
  tekshiriladi (24 soat amal qiladi). RBAC va ko'rish doirasi bot bilan bir xil servislardan olinadi.
- **Yoqish:** `PUBLIC_BASE_URL` `https://` bilan boshlanishi kerak — Telegram faqat HTTPS'ni qabul qiladi.
  Shunda bot menyu tugmasi va `/menu`da "📱 Ilovani ochish" paydo bo'ladi.
- Fayllar Telegram'da saqlanadi: yuklanganda nusxasi foydalanuvchining bot bilan chatiga ham tushadi.
  Bitta fayl — 20 MB gacha. Xarita OpenStreetMap va Leaflet (unpkg.com) orqali ishlaydi.

## Monitoring

Owner'ga avtomatik xabar boradi: Samsara webhook'lari belgilangan soat davomida kelmasa,
botda xato bo'lsa, alert biror chatga yetkazilmasa (bir xil xabar 10 daqiqada bir martadan ko'p emas).

## Ishga tushirish

```bash
cp .env.example .env
# .env: BOT_TOKEN, OWNER_TELEGRAM_ID, SAMSARA_WEBHOOK_SECRET ni to'ldiring
docker compose up -d --build
curl http://localhost:8000/health   # {"status": "ok"}
```

Konteyner ishga tushganda migratsiyalar avtomatik bajariladi. `BOT_MODE=polling` (standart)
bilan bot ochiq URL'siz ham ishlaydi. Telegram vaqtincha ishlamasa ham ilova darhol ochiladi
va Samsara webhook'larini qabul qiladi — Telegram sozlamalari fonda qayta urinib o'rnatiladi.

### Sinov alerti (Samsara token'isiz)

```bash
docker compose exec app python scripts/send_test_alert.py --type crash
docker compose exec app python scripts/send_test_alert.py --type speeding --vehicle "Isuzu 07"
```

## Backup va tiklash

`backup` konteyneri har kuni `pg_dump` oladi → `./backups/samsara_bot_YYYYMMDD_HHMMSS.dump`
(`BACKUP_KEEP_DAYS` kundan eskilari o'chiriladi). Owner `/admin` → 💾 orqali oxirgi nusxani
Telegram'da oladi; `BACKUP_SEND_TO_OWNER=true` bo'lsa — har kuni avtomatik.

Tiklash:

```bash
docker compose stop app
docker compose exec -T db pg_restore -U samsara_bot -d samsara_bot --clean --if-exists --no-owner \
  < backups/samsara_bot_YYYYMMDD_HHMMSS.dump
docker compose start app
```

## Testlar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Muhit o'zgaruvchilari

Qarang [.env.example](.env.example). Haqiqiy qiymatlar faqat server ustidagi `.env`da —
git'ga hech qachon commit qilinmaydi.
