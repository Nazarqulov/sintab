# Samsara Fleet Telegram Bot — Texnik Topshiriq (TZ)

**Status:** Loyihalashtirish bosqichi — tasdiqlangandan keyin 1-bosqichdan kodlash boshlanadi.
**Javob berilgan parametrlar:** Deploy — Oracle Cloud Always Free | Samsara API token — keyinroq beriladi | Hajm — 50+ foydalanuvchi, ko'p depo | Til — O'zbek/Rus/Ingliz

---

## 1. Loyiha maqsadi

Samsara (fleet management platformasi)dan kelgan alertlarni real vaqtda qabul qilib, tegishli Telegram guruh/topic'larga yo'naltiradigan, foydalanuvchilarni ro'yxatga oladigan va rol asosida huquqlarini boshqaradigan professional Telegram bot.

## 2. Texnik stek

| Qatlam | Tanlov |
|---|---|
| Bot framework | Python 3.11+, aiogram 3.x |
| Webhook server | FastAPI (Samsara → `POST /webhook/samsara`) |
| DB | PostgreSQL (50+ foydalanuvchi + ko'p depo uchun SQLite yetarli emas — concurrent yozish va indexlash kerak) |
| ORM / migratsiya | SQLAlchemy (async, asyncpg) + Alembic |
| Fon vazifalari | APScheduler (digest, eskalatsiya, health-check, backup) |
| Konfiguratsiya | `.env` (hech qanday sir kodga yozilmaydi) |
| Konteynerlash | Docker + docker-compose |
| Loglash | `logging` — fayl + konsol, maxfiy ma'lumotlarsiz |

## 3. Loyiha tuzilishi

```
app/
  bot/
    handlers/        # /start, /menu, /alerts, /admin, ...
    keyboards/        # inline/reply klaviaturalar
    middlewares/       # RBAC, throttling, i18n
    i18n/              # uz.json, ru.json, en.json
  api/
    webhook.py         # Samsara webhook endpoint + signature tekshirish
  services/
    samsara/           # REST API client (vehicles, drivers, location)
    alerts/             # formatlash, dedup, lifecycle
    routing/            # qoida bo'yicha guruh/topic tanlash
    reports/            # kunlik/haftalik statistika, CSV/Excel
  db/
    models/             # User, Role, Depot, Vehicle, Group, RoutingRule, Alert, AuditLog
    repositories/
  core/
    config.py           # .env o'qish, Pydantic Settings
    permissions.py       # RBAC flag'lari va middleware logikasi
  utils/
tests/
  test_permissions.py
  test_webhook_parsing.py
docker-compose.yml
Dockerfile
alembic/
.env.example
```

## 4. Ma'lumotlar bazasi — asosiy modellar

- **users**: `telegram_id`, `username`, `full_name`, `phone`, `position`, `role`, `status` (pending/active/blocked), `language`, `timezone`, `muted_until`, `created_at`
- **depots**: `id`, `name` — ko'p depo bo'lgani uchun alohida jadval
- **user_depots**: `user_id`, `depot_id` — Manager/Dispatcher bir nechta depoga bog'lanishi mumkin (ko'p-ko'p); Owner/Admin uchun bo'sh = barcha depolar
- **permissions**: granular flag'lar (quyida 7-bo'limda), `role_permissions` jadvali orqali rolga bog'lanadi + har foydalanuvchiga individual override (Owner tomonidan)
- **vehicles**, **drivers**: Samsara'dan keladigan/keshlangan ma'lumotlar, `depot_id` bilan
- **groups**: `chat_id`, `topic_id` (forum topic bo'lsa), `depot_id`, `bound_by`, `bound_at`
- **routing_rules**: `alert_type`, `severity`, `depot_id`, `vehicle_tag` → `group_id` (qaysi kombinatsiya qaysi guruhga boradi)
- **alerts**: `samsara_alert_id` (unique — dedup uchun), `type`, `severity`, `vehicle_id`, `driver_id`, `status` (new/acknowledged/resolved), `acknowledged_by`, `acknowledged_at`, `resolved_by`, `resolved_at`, `escalated_at`, `payload` (JSON)
- **audit_log**: `actor_id`, `action`, `target`, `details`, `created_at`

Indexlar: `alerts.status`, `alerts.vehicle_id`, `users.telegram_id` (unique), `routing_rules(depot_id, alert_type, severity)`.

## 5. Samsara integratsiyasi

- `POST /webhook/samsara` — Samsara rasmiy hujjatiga muvofiq **HMAC imzo** tekshiriladi; imzosiz/noto'g'ri so'rov → `401`.
- Qo'llanadigan alert turlari: tezlik oshirish, geofence kirish/chiqish, harsh braking/acceleration, dvigatel fault code, past yoqilg'i, texnik xizmat muddati, HOS (Hours of Service) buzilishi, qurilma uzilishi, vehicle idle.
- Xabar formati: severity emoji (🔴 Critical / 🟠 Warning / 🟢 Info), mashina, haydovchi, vaqt (foydalanuvchi timezone'ida), joylashuv (Google Maps link), tavsif.
- **Routing**: `routing_rules` jadvali orqali alert turi + severity + depot/tag kombinatsiyasiga qarab guruh/topic tanlanadi (admin panel orqali sozlanadi).
- **Dedup**: `samsara_alert_id` unique constraint — takror kelgan alert qayta yuborilmaydi.
- **Queue + retry**: xabar yuborish navbati, Telegram `429` (rate limit) hisobga olinadi, exponential backoff.
- **Inline tugmalar**: `✅ Qabul qildim`, `👤 Menga biriktir`, `🗺 Xaritada ko'rish`, `🔕 Mute 1 soat` — bosilgan harakat `alerts`/`audit_log` jadvaliga yoziladi (kim, qachon).
- **REST API** (`api.samsara.com`, Bearer token): mashina holati, joylashuvi, haydovchi, yoqilg'i darajasi — so'rov bo'yicha `/vehicles`, `/drivers` buyruqlarida ishlatiladi.

> Samsara API token/webhook secret hali yo'q — 2-bosqichda mock payload bilan ishlab, token kelganda real ulanish qilinadi.

## 6. Ro'yxatdan o'tish oqimi

1. `/start` → foydalanuvchi `pending` holatda bazaga yoziladi.
2. Ism-familiya, telefon (contact tugmasi), lavozim, bo'lim/depo so'raladi.
3. Owner/Admin'ga "Yangi so'rov" xabari + `[✅ Tasdiqlash (rol+depo tanlash)] [❌ Rad etish]` keladi.
4. Tasdiqlanmaguncha foydalanuvchi hech narsa ko'rmaydi (middleware darajasida bloklanadi).
5. Bloklash/o'chirish/rol-depo o'zgartirish — barchasi `audit_log`ga yoziladi.

## 7. Rollar va huquqlar (RBAC)

**Rollar:** Owner, Admin, Dispatcher, Manager, Viewer, Driver, Blocked/Pending.

**Depo darajasi** (ko'p depo bo'lgani uchun): Dispatcher/Manager/Driver faqat o'ziga bog'langan depo(lar) ma'lumotini ko'radi; Owner/Admin — barcha depolar.

**Granular permission flag'lar:** `view_alerts`, `ack_alerts`, `resolve_alerts`, `view_vehicles`, `view_reports`, `export_reports`, `manage_users`, `manage_roles`, `manage_groups`, `manage_routing`, `send_broadcast`, `manage_settings`, `view_audit_log`.

Har buyruq/tugma bosilishida **middleware** orqali tekshiriladi. Owner har bir foydalanuvchiga individual flag qo'shishi/olib tashlashi mumkin (role default'idan tashqari override).

## 8. Bot menyusi va buyruqlar

```
/start      /help       /menu       /alerts     /vehicles
/drivers    /reports    /settings   /profile    /admin
/users      /groups     /broadcast  /stats      /status
/mute       /unmute     /cancel     /bind (guruhda)
```

Ko'rinish rolga qarab farqlanadi. **Texnik eslatma:** Telegram `setMyCommands` metodi `scope: chat` bilan har bir foydalanuvchining shaxsiy (DM) chatiga alohida buyruqlar ro'yxati o'rnatishga ruxsat beradi — foydalanuvchi tasdiqlangach/roli o'zgargach, uning shaxsiy menyusi avtomatik yangilanadi. Guruh chatlarida buyruqlar umumiy bo'ladi, shu sababli guruhda faqat `/bind` kabi umumiy buyruqlar ko'rsatiladi.

Menyu: inline + reply klaviatura, sahifalash (pagination), "⬅️ Orqaga" / "🏠 Bosh menyu" tugmalari bilan.

## 9. Qo'shimcha funksiyalar

- **i18n (3 til: UZ lotin, RU, EN)**: JSON tarjima fayllari + aiogram middleware, `/settings` orqali tanlanadi, default — foydalanuvchi Telegram client tiliga qarab taxmin qilinadi (keyin o'zi o'zgartiradi).
- **Hisobotlar**: kunlik/haftalik statistika, eng ko'p alert beruvchi mashina/haydovchi, javob berish vaqti (response time) — CSV/Excel eksport (`openpyxl`).
- **Smart digest**: kechasi kelgan low-priority alertlar ertalab bitta xulosa qilib yuboriladi (APScheduler cron).
- **Eskalatsiya**: Critical alert sozlanadigan N daqiqa ichida qabul qilinmasa, yuqori rolga/boshqa guruhga qayta yuboriladi (fon job, `settings` jadvalidan N o'qiydi).
- **Guruh bog'lash**: botni guruhga qo'shib `/bind` — guruh (va forum topic, agar bo'lsa) ro'yxatga olinadi, depo tanlanadi.
- **Admin ogohlantirish**: Samsara webhook N daqiqa kelmasa yoki botda xatolik bo'lsa — Owner'ga darhol xabar (health-check job).
- **Qidiruv**: mashina raqami yoki haydovchi ismi bo'yicha `/vehicles` ichida qidiruv.
- **Backup**: bazaning kunlik avtomatik eksporti (dump), Owner'ga yuboriladi yoki belgilangan saqlash joyiga yoziladi.

## 10. Xavfsizlik

- Barcha sirlar faqat `.env`'da (git-ignore'da); kodga/hujjatga yozilmaydi.
- Webhook imzosi majburiy tekshiriladi; kiruvchi ma'lumotlar Pydantic bilan validatsiya qilinadi.
- Foydalanuvchi buyruqlari uchun rate limiting (anti-spam throttling middleware).
- Loglarda token/parol/shaxsiy ma'lumot ko'rinmaydi.
- `OWNER_TELEGRAM_ID` — bot ishga tushganda `.env`dan o'qilib, shu ID avtomatik Owner qilinadi.

## 11. Hajm bo'yicha eslatmalar (50+ foydalanuvchi, ko'p depo)

- PostgreSQL + async connection pool (SQLAlchemy async + asyncpg) — SQLite tanlanmadi.
- `routing_rules` jadvali moslashuvchan bo'lishi kerak (depo + severity + alert turi kombinatsiyasi), chunki depo soni ko'payishi mumkin.
- Manager/Dispatcher ko'rish doirasi `user_depots` orqali cheklanadi — har bir so'rovda depo filtri qo'llanadi.
- Guruh/topic soni ko'payadi — `/groups` admin panelida ro'yxat sahifalanadi (pagination).

## 12. Bosqichlar rejasi

| Bosqich | Mazmun |
|---|---|
| 1 | Arxitektura, DB sxema, Alembic migratsiyalar, Docker skeleton |
| 2 | Samsara webhook (mock payload bilan boshlanadi) + formatlash + routing + queue/retry + inline tugmalar + lifecycle |
| 3 | Ro'yxatdan o'tish + RBAC (rol+depo) + admin tasdiqlash oqimi + audit log |
| 4 | To'liq menyu/buyruqlar + i18n (3 til) + hisobotlar + smart digest + eskalatsiya + qidiruv + backup |
| 5 | Oracle Cloud Always Free'ga deploy: VM, Docker, Caddy/Nginx + Let's Encrypt, DuckDNS/Cloudflare Tunnel, `setWebhook`, Samsara dashboard ulash, test alert, systemd/restart policy |

Har bosqich tugagach: to'liq ishlaydigan kod + ishga tushirish buyruqlari + tekshirish usuli (masalan test alert yuborish, `/status` orqali health-check) beriladi.

## 13. Ochiq savollar (keyinroq hal qilinadi, ishni to'xtatmaydi)

- **Samsara API token / webhook secret** — siz aytganingizdek keyinroq beriladi; shu vaqtgacha 2-bosqich mock (soxta) payload bilan ishlab chiqiladi, token kelganda faqat `.env`ga qo'yiladi, kod o'zgarmaydi.
- **Domen** — agar o'zingizda domen bo'lmasa, DuckDNS (bepul subdomain) + Let's Encrypt ishlatiladi; agar domeningiz bo'lsa, ayting — Cloudflare Tunnel yoki to'g'ridan-to'g'ri A-record bilan ulaymiz (5-bosqichda aniqlashtiriladi).
- **Depolar ro'yxati va boshlang'ich routing qoidalari** — 3/4-bosqichda admin panel orqali o'zingiz sozlaysiz (bo'sh jadval bilan boshlanadi, kod kutib turmaydi).

## 14. `.env.example` namunasi

```
BOT_TOKEN=<telegram_bot_token>
SAMSARA_API_TOKEN=<keyinroq_qoyiladi>
SAMSARA_WEBHOOK_SECRET=<keyinroq_qoyiladi>
OWNER_TELEGRAM_ID=<sizning_telegram_id>
DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/samsara_bot
DEFAULT_LANGUAGE=uz
```

> Haqiqiy token/parol qiymatlari bu faylga emas, faqat server ustidagi `.env`ga yoziladi (git-ignore'da).

---

### Keyingi qadam

Ushbu TZ'ni tasdiqlasangiz, **1-bosqich** (arxitektura + DB sxema + Docker skeleton)dan boshlayman. Agar biror bo'lim noto'g'ri yoki o'zgartirish kerak bo'lsa — aytib bering.
