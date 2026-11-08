from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request, status

from app.bot.bot_instance import bot, dp
from app.core.config import settings

router = APIRouter()


@router.post("/webhook/telegram/{secret_path}")
async def telegram_webhook(secret_path: str, request: Request) -> dict:
    if secret_path != settings.telegram_webhook_secret_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_webhook_update(bot=bot, update=update)
    return {"ok": True}
