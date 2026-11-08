from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.users import get_user_by_telegram_id


class DbSessionMiddleware(BaseMiddleware):
    """Har update uchun bitta DB sessiya va joriy foydalanuvchi (`session`, `user`)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            tg_user = data.get("event_from_user")
            data["user"] = await get_user_by_telegram_id(session, tg_user.id) if tg_user else None
            return await handler(event, data)
