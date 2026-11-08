from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from app.bot.middlewares.access import denial_key
from app.core.i18n import t
from app.db.models import User

router = Router(name="fallback")
router.message.filter(F.chat.type == ChatType.PRIVATE)


@router.message(flags={"public": True})
async def fallback(message: Message, user: User | None, lang: str) -> None:
    await message.answer(t(denial_key(user) or "common.unknown", lang))
