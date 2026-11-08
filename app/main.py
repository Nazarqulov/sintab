import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from aiogram.exceptions import TelegramAPIError
from fastapi import FastAPI

from app.api import samsara_webhook, telegram_webhook
from app.bot.bot_instance import bot, dp
from app.bot.commands import setup_default_commands
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.services.alerts.jobs import run_digest, run_escalations
from app.services.alerts.sender import AlertSender
from app.services.samsara.client import SamsaraClient
from app.services.samsara.sync import sync_fleet
from app.services.scheduler import Scheduler
from app.services.users import ensure_owner

logger = logging.getLogger(__name__)


async def fleet_sync_job() -> None:
    async with SamsaraClient(settings.samsara_api_token, settings.samsara_api_base_url) as client:
        async with AsyncSessionLocal() as session:
            vehicles, drivers = await sync_fleet(session, client)
    logger.info("Samsara sinxron: %s mashina, %s haydovchi", vehicles, drivers)


def build_scheduler(sender: AlertSender) -> Scheduler:
    scheduler = Scheduler()

    async def escalation_job() -> None:
        async with AsyncSessionLocal() as session:
            sender.enqueue(await run_escalations(session, datetime.now(UTC)))

    async def digest_job() -> None:
        async with AsyncSessionLocal() as session:
            await run_digest(session, bot, datetime.now(UTC))

    scheduler.every(60, "escalation", escalation_job)
    scheduler.every(300, "digest", digest_job)
    if settings.samsara_api_token:
        scheduler.every(6 * 3600, "fleet-sync", fleet_sync_job, initial_delay=10)
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    async with AsyncSessionLocal() as session:
        await ensure_owner(session, settings.owner_telegram_id)

    sender = AlertSender(bot, AsyncSessionLocal)
    await sender.start()
    app.state.alert_sender = sender
    scheduler = build_scheduler(sender)

    try:
        await setup_default_commands(bot)
    except TelegramAPIError as error:
        logger.warning("Standart buyruqlar o'rnatilmadi: %s", error)

    polling: asyncio.Task | None = None
    if settings.bot_mode == "polling":
        await bot.delete_webhook()
        polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    elif settings.public_base_url:
        url = f"{settings.public_base_url.rstrip('/')}/webhook/telegram/{settings.telegram_webhook_secret_path}"
        await bot.set_webhook(url, allowed_updates=dp.resolve_used_update_types())

    try:
        yield
    finally:
        await scheduler.stop()
        if polling is not None:
            with contextlib.suppress(RuntimeError):
                await dp.stop_polling()
            polling.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await polling
        await sender.stop()
        await bot.session.close()


app = FastAPI(title="Samsara Fleet Bot", lifespan=lifespan)
app.include_router(telegram_webhook.router)
app.include_router(samsara_webhook.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
