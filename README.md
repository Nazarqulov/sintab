# Samsara Fleet Telegram Bot

To'liq texnik topshiriq: [TZ.md](TZ.md).

**Holat:** 3-bosqich tayyor — Samsara webhook va alertlar (2), ro'yxatdan o'tish, RBAC,
foydalanuvchi va depo boshqaruvi (3). Keyingilari: menyu/i18n/hisobotlar (4), deploy (5).

## Foydalanuvchilar va huquqlar

- `/start` — ro'yxatdan o'tish: ism → telefon (faqat o'z raqami, tugma orqali) → lavozim → depo.
  Telefon Samsara'dagi haydovchi raqamiga mos kelsa, avtomatik bog'lanadi.
- So'rov barcha Owner/Admin'larga keladi: **Tasdiqlash** (rol → depolar) yoki **Rad etish**
  (rad etilgan foydalanuvchi bloklanadi — qayta spam qila olmaydi; `/users`dan tiklash mumkin).
  Tasdiqlanmaguncha foydalanuvchi hech narsani ko'rmaydi.
- `/users` — ro'yxat, rol, depolar, bloklash, o'chirish; Owner uchun individual huquqlar.
- `/depots` — depolarni qo'shish/o'chirish. `/cancel` — joriy amalni bekor qilish.
- Ierarxiya: Owner'ga hech kim tega olmaydi; Admin'ni faqat Owner boshqaradi va tayinlaydi;
  hech kim o'zini o'zgartira olmaydi. Har amal `audit_log`ga yoziladi.
- Har handler `flags={"permission": ...}` bilan belgilanadi; belgilanmaganiga faqat faol
  foydalanuvchi kiradi. Guruhlarda begonalarga javob berilmaydi. Anti-spam: 8 ta tezkor, keyin 1/s.

Owner botga avval `/start` yozishi kerak — aks holda Telegram so'rov va alertlarni unga
yetkaza olmaydi.

## Ishga tushirish (lokal)

```bash
cp .env.example .env
# .env: BOT_TOKEN, OWNER_TELEGRAM_ID, SAMSARA_WEBHOOK_SECRET ni to'ldiring

docker compose up -d --build
curl http://localhost:8000/health   # {"status": "ok"}
```

Konteyner ishga tushganda `alembic upgrade head` avtomatik bajariladi. `BOT_MODE=polling`
(standart) bilan bot ochiq URL'siz ham ishlaydi — tugmalarni lokal sinash mumkin.

## Sinov alerti (Samsara token'isiz)

```bash
docker compose exec app python scripts/send_test_alert.py --type crash
docker compose exec app python scripts/send_test_alert.py --type speeding --vehicle "Isuzu 07"
```

Skript `.env`dagi `SAMSARA_WEBHOOK_SECRET` bilan imzolangan namunaviy alert yuboradi.
Routing qoidasi bo'lmasa, alert Owner'ning shaxsiy chatiga keladi (Owner botga avval
`/start` yozgan bo'lishi kerak).

## Alert oqimi

1. `POST /webhook/samsara` — HMAC imzo + vaqt tamg'asi (5 daqiqa) tekshiriladi, aks holda `401`.
2. Payload (Webhooks 2.0 `AlertIncident` yoki eski `Alert`) o'qiladi, turi va severity aniqlanadi.
3. `eventId` bo'yicha takrorlar tashlanadi; mashina/haydovchi bazaga yoziladi.
4. Faol mute bo'lsa — saqlanadi, lekin yuborilmaydi.
5. `routing_rules` bo'yicha guruh(lar) tanlanadi (NULL = har qanday); hech biri mos kelmasa — Owner.
6. Har bir chat uchun `alert_deliveries` yozuvi — worker navbat bilan yuboradi (429, retry,
   Telegram limitlari). Navbat bazada, restart'da yo'qolmaydi.
7. Tugmalar: Qabul qildim → Yopish, Menga biriktir, Xarita, Mute 1 soat. Har amal huquq
   bilan tekshiriladi, audit log'ga yoziladi va barcha chatlardagi xabar yangilanadi.

Severity standartlarini `app_settings` jadvalidagi `severity_overrides` kaliti orqali
o'zgartirish mumkin (masalan `{"speeding": "critical"}`).

## Testlar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Muhit o'zgaruvchilari

Qarang [.env.example](.env.example). Haqiqiy qiymatlar faqat server ustidagi `.env`da —
git'ga hech qachon commit qilinmaydi.
