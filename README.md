# Samsara Fleet Telegram Bot

To'liq texnik topshiriq: [TZ.md](TZ.md).

**Holat:** 1-bosqich — arxitektura, DB sxemasi, Docker skeleton. Samsara webhook
logikasi, ro'yxatdan o'tish/RBAC oqimi, to'liq menyu/i18n va deploy keyingi
bosqichlarda qo'shiladi (TZ.md, 12-bo'lim).

## Talablar

- Docker + Docker Compose
- Telegram bot tokeni ([@BotFather](https://t.me/BotFather))

## Ishga tushirish (lokal)

```bash
cp .env.example .env
# .env faylida BOT_TOKEN va OWNER_TELEGRAM_ID'ni to'ldiring

docker compose up -d --build
curl http://localhost:8000/health   # {"status": "ok"}
```

`docker-entrypoint.sh` konteyner ishga tushganda avtomatik `alembic upgrade head`
bajaradi — qo'lda migratsiya ishga tushirish shart emas.

## Testlar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Loyiha tuzilishi

Qarang [TZ.md](TZ.md), 3-bo'lim.

## Muhit o'zgaruvchilari

Qarang [.env.example](.env.example). Haqiqiy qiymatlar faqat server ustidagi
`.env` faylida saqlanadi — git'ga hech qachon commit qilinmaydi.
