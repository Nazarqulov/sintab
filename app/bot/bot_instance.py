from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.bot.handlers import alerts, start
from app.core.config import settings

# Standart 60s o'rniga: Telegram javob bermasa, alert navbati uzoq tiqilib qolmasin.
TELEGRAM_TIMEOUT_SECONDS = 15

bot = Bot(
    token=settings.bot_token,
    session=AiohttpSession(timeout=TELEGRAM_TIMEOUT_SECONDS),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
dp.include_router(start.router)
dp.include_router(alerts.router)
