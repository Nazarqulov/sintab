"""RBAC: har handler `flags={"permission": ...}` yoki `flags={"public": True}` bilan belgilanadi.

Belgilanmagan handler'ga faqat faol foydalanuvchi kira oladi (xavfsiz standart).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot import texts
from app.db.models import User, UserStatus
from app.services.users import has_permission


def denial_reason(user: User | None) -> str | None:
    if user is None:
        return texts.NOT_REGISTERED
    if user.status == UserStatus.PENDING:
        return texts.PENDING
    if user.status == UserStatus.BLOCKED:
        return texts.BLOCKED
    return None


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if get_flag(data, "public"):
            return await handler(event, data)

        user: User | None = data.get("user")
        reason = denial_reason(user)
        permission = get_flag(data, "permission")
        if reason is None and permission is not None and not await has_permission(user, permission):
            reason = texts.NO_PERMISSION
        if reason is None:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer(reason, show_alert=True)
        elif isinstance(event, Message) and event.chat.type == ChatType.PRIVATE:
            # Guruhlarda begona a'zolarning buyruqlariga javob berib chatni to'ldirmaymiz.
            await event.answer(reason)
        return None
