from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.bot.handlers import alerts, depots, fallback, groups, personal, registration, users_admin
from app.bot.middlewares.access import AccessMiddleware
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.throttling import ThrottlingMiddleware
from app.core.config import settings
from app.db.session import AsyncSessionLocal

# Standart 60s o'rniga: Telegram javob bermasa, alert navbati uzoq tiqilib qolmasin.
TELEGRAM_TIMEOUT_SECONDS = 15


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()

    dispatcher.update.outer_middleware(DbSessionMiddleware(AsyncSessionLocal))
    throttling = ThrottlingMiddleware()
    dispatcher.message.outer_middleware(throttling)
    dispatcher.callback_query.outer_middleware(throttling)
    # Inner middleware ota router'da — barcha ichki router handler'lariga meros bo'lib o'tadi.
    dispatcher.message.middleware(AccessMiddleware())
    dispatcher.callback_query.middleware(AccessMiddleware())

    dispatcher.include_routers(
        registration.router, personal.router, alerts.router, users_admin.router, depots.router, groups.router
    )
    dispatcher.include_router(fallback.router)  # oxirida bo'lishi shart
    return dispatcher


bot = Bot(
    token=settings.bot_token,
    session=AiohttpSession(timeout=TELEGRAM_TIMEOUT_SECONDS),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = build_dispatcher()
