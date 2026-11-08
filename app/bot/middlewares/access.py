"""RBAC: har handler `flags={"permission": ...}` yoki `flags={"public": True}` bilan belgilanadi.

Belgilanmagan handler'ga faqat faol foydalanuvchi kira oladi (xavfsiz standart).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.core.i18n import t
from app.db.models import User, UserStatus
from app.services.users import has_permission


def denial_key(user: User | None) -> str | None:
    if user is None:
        return "access.not_registered"
    if user.status == UserStatus.PENDING:
        return "access.pending"
    if user.status == UserStatus.BLOCKED:
        return "access.blocked"
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
        key = denial_key(user)
        permission = get_flag(data, "permission")
        if key is None and permission is not None and not await has_permission(user, permission):
            key = "access.no_permission"
        if key is None:
            return await handler(event, data)

        text = t(key, data.get("lang"))
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, Message) and event.chat.type == ChatType.PRIVATE:
            # Guruhlarda begona a'zolarning buyruqlariga javob berib chatni to'ldirmaymiz.
            await event.answer(text)
        return None
