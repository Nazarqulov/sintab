import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from app.bot.handlers import (
    admin,
    alerts,
    broadcast,
    browse,
    chat,
    depots,
    documents,
    fallback,
    fleet,
    groups,
    menu,
    personal,
    registration,
    reports,
    users_admin,
)
from app.bot.middlewares.access import AccessMiddleware
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.throttling import ThrottlingMiddleware
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.monitoring import notify_bot_error
from app.services.runtime import runtime

logger = logging.getLogger(__name__)

# Standart 60s o'rniga: Telegram javob bermasa, alert navbati uzoq tiqilib qolmasin.
TELEGRAM_TIMEOUT_SECONDS = 15


async def on_error(event: ErrorEvent) -> bool:
    logger.exception("Update'ni qayta ishlashda xato", exc_info=event.exception)
    if runtime.notifier is not None:
        await notify_bot_error(runtime.notifier, event.exception)
    return True


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()

    dispatcher.update.outer_middleware(DbSessionMiddleware(AsyncSessionLocal))
    throttling = ThrottlingMiddleware()
    dispatcher.message.outer_middleware(throttling)
    dispatcher.callback_query.outer_middleware(throttling)
    # Inner middleware ota router'da — barcha ichki router handler'lariga meros bo'lib o'tadi.
    dispatcher.message.middleware(AccessMiddleware())
    dispatcher.callback_query.middleware(AccessMiddleware())
    dispatcher.errors.register(on_error)

    # Tartib muhim: FSM holatidagi handler'lar (ro'yxatdan o'tish) birinchi, umumiy fallback oxirida.
    dispatcher.include_routers(
        registration.router,
        personal.router,
        menu.router,
        documents.router,
        chat.router,
        alerts.router,
        browse.router,
        fleet.router,
        reports.router,
        admin.router,
        broadcast.router,
        users_admin.router,
        depots.router,
        groups.router,
    )
    dispatcher.include_router(fallback.router)
    return dispatcher


bot = Bot(
    token=settings.bot_token,
    session=AiohttpSession(timeout=TELEGRAM_TIMEOUT_SECONDS),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = build_dispatcher()
