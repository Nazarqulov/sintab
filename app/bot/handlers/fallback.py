from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from app.bot.middlewares.access import denial_reason
from app.db.models import User

router = Router(name="fallback")
router.message.filter(F.chat.type == ChatType.PRIVATE)


@router.message(flags={"public": True})
async def fallback(message: Message, user: User | None) -> None:
    await message.answer(denial_reason(user) or "🤔 Bu buyruqni tushunmadim.")
