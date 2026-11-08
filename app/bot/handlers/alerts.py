from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.keyboards.alerts import AlertAction, AlertActionType
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.alerts.actions import ActionError, apply_action, refresh_alert_messages
from app.services.users import get_user_by_telegram_id

router = Router(name="alerts")


@router.callback_query(AlertAction.filter())
async def on_alert_action(callback: CallbackQuery, callback_data: AlertAction) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_user_by_telegram_id(session, callback.from_user.id)
        try:
            reply = await apply_action(session, callback_data.alert_id, callback_data.action, user)
        except ActionError as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer(reply)
        if callback_data.action != AlertActionType.MUTE:
            await refresh_alert_messages(callback.bot, session, callback_data.alert_id, settings.default_timezone)
