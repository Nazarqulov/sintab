from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    # To'liq ro'yxatdan o'tish (pending -> admin tasdiqlash -> rol/depo) oqimi 3-bosqichda qo'shiladi.
    await message.answer(
        "👋 Salom! Samsara Fleet Bot ishga tushdi.\n"
        "Ro'yxatdan o'tish va rol tizimi keyingi bosqichda qo'shiladi."
    )
