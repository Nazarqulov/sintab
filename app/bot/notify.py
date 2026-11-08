import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def send_safe(bot: Bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> bool:
    """Shaxsiy xabar: foydalanuvchi botni bloklagan bo'lsa ham asosiy amal to'xtamasin."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        return True
    except TelegramAPIError as error:
        logger.warning("Chat %s ga xabar yuborilmadi: %s", chat_id, error)
        return False
