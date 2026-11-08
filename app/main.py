import asyncio
import contextlib
import itertools
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from fastapi import FastAPI

from app.api import samsara_webhook, telegram_webhook
from app.bot.bot_instance import bot, dp
from app.bot.commands import setup_default_commands
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.core.i18n import t
from app.db.models import AlertDelivery
from app.services.alerts.jobs import run_digest, run_escalations
from app.services.alerts.sender import AlertSender
from app.services.backup import TELEGRAM_UPLOAD_LIMIT, latest_backup
from app.services.fleet_jobs import run_document_reminders, run_fuel_check, run_hos_warnings, run_shift_report
from app.services.monitoring import OwnerNotifier, check_webhook_silence, mark_once, owner_language
from app.services.runtime import runtime
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


async def send_daily_backup() -> None:
    """BACKUP_SEND_TO_OWNER=true bo'lsa — kuniga bir marta eng yangi backup Owner'ga."""
    backup = latest_backup(settings.backup_dir)
    if backup is None or backup.size > TELEGRAM_UPLOAD_LIMIT:
        return
    async with AsyncSessionLocal() as session:
        if not await mark_once(session, f"backup_sent:{backup.path.name}"):
            return
    await bot.send_document(settings.owner_telegram_id, FSInputFile(backup.path), caption=f"💾 {backup.path.name}")


def build_scheduler(sender: AlertSender, notifier: OwnerNotifier) -> Scheduler:
    scheduler = Scheduler()

    async def webhook_silence_job() -> None:
        async with AsyncSessionLocal() as session:
            await check_webhook_silence(
                session, notifier, settings.owner_telegram_id, settings.default_timezone, datetime.now(UTC)
            )

    async def escalation_job() -> None:
        async with AsyncSessionLocal() as session:
            sender.enqueue(await run_escalations(session, datetime.now(UTC)))

    async def digest_job() -> None:
        async with AsyncSessionLocal() as session:
            await run_digest(session, bot, datetime.now(UTC))

    async def samsara_job(job) -> None:
        async with SamsaraClient(settings.samsara_api_token, settings.samsara_api_base_url) as client:
            async with AsyncSessionLocal() as session:
                sender.enqueue(await job(session, client, datetime.now(UTC)))

    async def hos_job() -> None:
        await samsara_job(run_hos_warnings)

    async def fuel_job() -> None:
        await samsara_job(run_fuel_check)

    async def documents_job() -> None:
        async with AsyncSessionLocal() as session:
            await run_document_reminders(session, bot, datetime.now(UTC))

    async def shift_job() -> None:
        async with AsyncSessionLocal() as session:
            await run_shift_report(session, bot, datetime.now(UTC))

    scheduler.every(60, "escalation", escalation_job)
    scheduler.every(3600, "document-reminders", documents_job, initial_delay=90)
    scheduler.every(600, "shift-report", shift_job, initial_delay=120)
    scheduler.every(300, "digest", digest_job)
    scheduler.every(600, "webhook-silence", webhook_silence_job, initial_delay=60)
    if settings.backup_send_to_owner:
        scheduler.every(3600, "backup-send", send_daily_backup, initial_delay=120)
    if settings.samsara_api_token:
        scheduler.every(6 * 3600, "fleet-sync", fleet_sync_job, initial_delay=10)
        scheduler.every(600, "hos-warnings", hos_job, initial_delay=60)
        scheduler.every(1800, "fuel-check", fuel_job, initial_delay=45)
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    async with AsyncSessionLocal() as session:
        await ensure_owner(session, settings.owner_telegram_id)

    notifier = OwnerNotifier(bot, settings.owner_telegram_id)

    async def on_delivery_failure(delivery: AlertDelivery, error: Exception) -> None:
        async with AsyncSessionLocal() as session:
            lang = await owner_language(session, settings.owner_telegram_id)
        await notifier.notify(
            f"delivery:{delivery.chat_id}",
            t("monitor.delivery_failed", lang, chat=delivery.chat_id, alert=delivery.alert_id, error=str(error)[:300]),
        )

    sender = AlertSender(bot, AsyncSessionLocal, on_failure=on_delivery_failure)
    await sender.start()
    app.state.alert_sender = sender
    runtime.sender = sender
    runtime.notifier = notifier
    scheduler = build_scheduler(sender, notifier)

    # Telegram ishlamay turgan bo'lsa ham ilova (Samsara webhook) darhol ishga tushsin — Telegram sozlash fonda.
    telegram = asyncio.create_task(telegram_startup(), name="telegram-startup")

    try:
        yield
    finally:
        await scheduler.stop()
        if settings.bot_mode == "polling":
            with contextlib.suppress(RuntimeError):
                await dp.stop_polling()
        telegram.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await telegram
        await sender.stop()
        await bot.session.close()


async def telegram_startup() -> None:
    """Webhook/polling va buyruqlar menyusini o'rnatadi; Telegram'ga yetib bo'lmasa — kutib qayta uradi."""
    for attempt in itertools.count():
        try:
            if settings.bot_mode == "polling":
                await bot.delete_webhook()
            elif settings.public_base_url:
                url = f"{settings.public_base_url.rstrip('/')}/webhook/telegram/{settings.telegram_webhook_secret_path}"
                await bot.set_webhook(url, allowed_updates=dp.resolve_used_update_types())
            await setup_default_commands(bot)
            break
        except TelegramAPIError as error:
            delay = min(5 * 2**attempt, 300)
            logger.warning("Telegram sozlanmadi (%s) — %ss keyin qayta", error, delay)
            await asyncio.sleep(delay)
    if settings.bot_mode == "polling":
        await dp.start_polling(bot, handle_signals=False)


app = FastAPI(title="Samsara Fleet Bot", lifespan=lifespan)
app.include_router(telegram_webhook.router)
app.include_router(samsara_webhook.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
