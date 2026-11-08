import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import AlertDelivery, DeliveryStatus
from app.services.alerts.render import delivery_context, render_delivery

logger = logging.getLogger(__name__)

# Telegram cheklovlari: guruhga ~20 xabar/daqiqa, bitta chatga ~1 xabar/s, umumiy ~30 xabar/s.
GROUP_CHAT_INTERVAL = 3.0
PRIVATE_CHAT_INTERVAL = 1.0
GLOBAL_INTERVAL = 1 / 25
MAX_ATTEMPTS = 5
MAX_RATE_LIMIT_WAITS = 20


class AlertSender:
    """Bazadagi `pending` yetkazishlarni bitta worker orqali navbat bilan yuboradi.

    Ish holati bazada bo'lgani uchun restart'dan keyin `start()` qolganlarini qayta navbatga qo'yadi.
    """

    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        on_failure: Callable[[AlertDelivery, Exception], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._on_failure = on_failure
        self._sleep = sleep
        self._clock = clock
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._last_sent_per_chat: dict[int, float] = {}
        self._last_sent = float("-inf")

    async def start(self) -> None:
        async with self._session_factory() as session:
            pending = await session.scalars(
                select(AlertDelivery.id).where(AlertDelivery.status == DeliveryStatus.PENDING).order_by(AlertDelivery.id)
            )
            self.enqueue(pending.all())
        self._task = asyncio.create_task(self._run(), name="alert-sender")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def enqueue(self, delivery_ids: list[int]) -> None:
        for delivery_id in delivery_ids:
            self._queue.put_nowait(delivery_id)

    async def _run(self) -> None:
        while True:
            delivery_id = await self._queue.get()
            try:
                await self.process(delivery_id)
            except Exception:
                logger.exception("Yetkazish %s da kutilmagan xato", delivery_id)
            finally:
                self._queue.task_done()

    async def process(self, delivery_id: int) -> None:
        async with self._session_factory() as session:
            delivery = await session.scalar(
                select(AlertDelivery)
                .options(selectinload(AlertDelivery.alert))
                .where(AlertDelivery.id == delivery_id)
            )
            if delivery is None or delivery.status != DeliveryStatus.PENDING:
                return

            text, markup = render_delivery(delivery.alert, delivery, await delivery_context(session, delivery))
            rate_limit_waits = 0
            failures = 0
            while True:
                await self._throttle(delivery.chat_id)
                delivery.attempts += 1
                try:
                    message = await self._bot.send_message(
                        chat_id=delivery.chat_id,
                        text=text,
                        message_thread_id=delivery.topic_id,
                        reply_markup=markup,
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                except TelegramRetryAfter as error:
                    rate_limit_waits += 1
                    if rate_limit_waits <= MAX_RATE_LIMIT_WAITS:
                        logger.warning("Telegram 429: chat %s, %ss kutamiz", delivery.chat_id, error.retry_after)
                        await self._sleep(error.retry_after)
                        continue
                    await self._fail(session, delivery, error)
                    return
                except (TelegramForbiddenError, TelegramBadRequest) as error:
                    # Bot guruhdan chiqarilgan / chat topilmadi — qayta urinish foydasiz.
                    await self._fail(session, delivery, error)
                    return
                except Exception as error:
                    failures += 1
                    delivery.last_error = str(error)[:500]
                    if failures >= MAX_ATTEMPTS:
                        await self._fail(session, delivery, error)
                        return
                    await session.commit()
                    backoff = min(2**failures, 60)
                    logger.warning("Yetkazish %s: %s — %ss keyin qayta", delivery.id, error, backoff)
                    await self._sleep(backoff)
                    continue

                delivery.status = DeliveryStatus.SENT
                delivery.message_id = message.message_id
                delivery.sent_at = datetime.now(UTC)
                await session.commit()
                return

    async def _fail(self, session: AsyncSession, delivery: AlertDelivery, error: Exception) -> None:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = str(error)[:500]
        await session.commit()
        logger.error("Yetkazish %s (chat %s) muvaffaqiyatsiz: %s", delivery.id, delivery.chat_id, error)
        if self._on_failure is not None:
            try:
                await self._on_failure(delivery, error)
            except Exception:
                logger.exception("on_failure callback xatosi")

    async def _throttle(self, chat_id: int) -> None:
        interval = GROUP_CHAT_INTERVAL if chat_id < 0 else PRIVATE_CHAT_INTERVAL
        now = self._clock()
        wait = max(
            self._last_sent_per_chat.get(chat_id, float("-inf")) + interval - now,
            self._last_sent + GLOBAL_INTERVAL - now,
            0.0,
        )
        if wait > 0:
            await self._sleep(wait)
        sent_at = self._clock()
        self._last_sent_per_chat[chat_id] = sent_at
        self._last_sent = sent_at
