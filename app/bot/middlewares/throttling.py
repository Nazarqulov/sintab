import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class ThrottlingMiddleware(BaseMiddleware):
    """Anti-spam: har foydalanuvchiga token-bucket (`burst` ta zudlik bilan, keyin `rate` ta/soniya)."""

    def __init__(self, rate: float = 1.0, burst: int = 5, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._buckets: dict[int, tuple[float, float]] = {}

    def allow(self, user_id: int) -> bool:
        now = self._clock()
        tokens, updated = self._buckets.get(user_id, (float(self._burst), now))
        tokens = min(self._burst, tokens + (now - updated) * self._rate)
        if tokens < 1:
            self._buckets[user_id] = (tokens, now)
            return False
        self._buckets[user_id] = (tokens - 1, now)
        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None or self.allow(tg_user.id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("⏳ Juda tez. Biroz kuting.")
        return None
