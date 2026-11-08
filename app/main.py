import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import samsara_webhook, telegram_webhook
from app.bot.bot_instance import bot, dp
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.services.alerts.sender import AlertSender
from app.services.samsara.client import SamsaraClient
from app.services.samsara.sync import sync_fleet
from app.services.users import ensure_owner

logger = logging.getLogger(__name__)


async def _initial_fleet_sync() -> None:
    try:
        async with SamsaraClient(settings.samsara_api_token, settings.samsara_api_base_url) as client:
            async with AsyncSessionLocal() as session:
                vehicles, drivers = await sync_fleet(session, client)
        logger.info("Samsara sinxron: %s mashina, %s haydovchi", vehicles, drivers)
    except Exception:
        logger.exception("Samsara'dan mashina/haydovchilarni olishda xato")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    async with AsyncSessionLocal() as session:
        await ensure_owner(session, settings.owner_telegram_id)

    sender = AlertSender(bot, AsyncSessionLocal)
    await sender.start()
    app.state.alert_sender = sender

    background: list[asyncio.Task] = []
    if settings.bot_mode == "polling":
        await bot.delete_webhook()
        background.append(asyncio.create_task(dp.start_polling(bot, handle_signals=False)))
    elif settings.public_base_url:
        url = f"{settings.public_base_url.rstrip('/')}/webhook/telegram/{settings.telegram_webhook_secret_path}"
        await bot.set_webhook(url, allowed_updates=dp.resolve_used_update_types())
    if settings.samsara_api_token:
        background.append(asyncio.create_task(_initial_fleet_sync()))

    try:
        yield
    finally:
        if settings.bot_mode == "polling":
            with contextlib.suppress(RuntimeError):
                await dp.stop_polling()
        for task in background:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await sender.stop()
        await bot.session.close()


app = FastAPI(title="Samsara Fleet Bot", lifespan=lifespan)
app.include_router(telegram_webhook.router)
app.include_router(samsara_webhook.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
