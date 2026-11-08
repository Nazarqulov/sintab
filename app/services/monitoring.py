"""Owner'ga texnik ogohlantirishlar: webhook jimligi, bot xatolari, yetkazib bo'lmagan alertlar."""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.notify import send_safe
from app.core.i18n import t
from app.db.models import AppSetting, NotificationMark, User
from app.services.alerts.formatter import as_utc, format_time
from app.services.app_settings import get_setting

logger = logging.getLogger(__name__)

LAST_WEBHOOK_SETTING = "last_webhook_at"


class OwnerNotifier:
    """Bir xil turdagi ogohlantirish `cooldown` soniyada bir martadan ko'p yuborilmaydi (xabar bo'roni bo'lmasin)."""

    def __init__(self, bot: Bot, owner_chat_id: int, cooldown: float = 600, clock: Callable[[], float] = time.monotonic):
        self._bot = bot
        self._owner_chat_id = owner_chat_id
        self._cooldown = cooldown
        self._clock = clock
        self._last: dict[str, float] = {}

    async def notify(self, key: str, text: str) -> bool:
        now = self._clock()
        if now - self._last.get(key, float("-inf")) < self._cooldown:
            return False
        self._last[key] = now
        return await send_safe(self._bot, self._owner_chat_id, text)


async def owner_language(session: AsyncSession, owner_chat_id: int) -> str:
    owner = await session.scalar(select(User).where(User.telegram_id == owner_chat_id))
    return owner.language if owner else "uz"


async def record_webhook(session: AsyncSession, now: datetime) -> None:
    row = await session.get(AppSetting, LAST_WEBHOOK_SETTING)
    value = {"v": now.isoformat()}
    if row is None:
        session.add(AppSetting(key=LAST_WEBHOOK_SETTING, value=value))
    else:
        row.value = value
    await session.commit()


async def last_webhook_at(session: AsyncSession) -> datetime | None:
    row = await session.get(AppSetting, LAST_WEBHOOK_SETTING)
    if row is None or not isinstance(row.value, dict) or "v" not in row.value:
        return None
    return as_utc(datetime.fromisoformat(row.value["v"]))


async def mark_once(session: AsyncSession, key: str) -> bool:
    """Kalit birinchi marta belgilanayotgan bo'lsa True (restart'dan keyin ham takrorlanmaydi)."""
    if await session.get(NotificationMark, key) is not None:
        return False
    session.add(NotificationMark(key=key))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True


async def check_webhook_silence(
    session: AsyncSession, notifier: OwnerNotifier, owner_chat_id: int, tz_name: str, now: datetime
) -> bool:
    hours = await get_setting(session, "webhook_silence_hours")
    last = await last_webhook_at(session)
    if not hours or last is None or now - last < timedelta(hours=hours):
        return False
    # Har bir jimlik davri uchun bitta xabar: kalit oxirgi webhook vaqtiga bog'langan.
    if not await mark_once(session, f"webhook_silence:{last.isoformat()}"):
        return False
    lang = await owner_language(session, owner_chat_id)
    text = t("monitor.webhook_silence", lang, hours=hours, last=format_time(last, tz_name))
    return await notifier.notify("webhook_silence", text)


async def notify_bot_error(notifier: OwnerNotifier, error: Exception, lang: str = "uz") -> None:
    text = t("monitor.bot_error", lang, error=escape(f"{type(error).__name__}: {error}")[:500])
    await notifier.notify(f"error:{type(error).__name__}", text)

