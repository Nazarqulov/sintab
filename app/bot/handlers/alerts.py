import contextlib

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.alerts import AlertAction, AlertActionType, AlertReason, reasons_keyboard
from app.bot.handlers.browse import render_detail
from app.core.i18n import t
from app.db.models import Alert, User
from app.services.alerts.actions import (
    ActionError,
    add_note,
    apply_action,
    check_can_resolve,
    find_alert_by_message,
    refresh_alert_messages,
)

router = Router(name="alerts")


@router.callback_query(AlertAction.filter(F.action == AlertActionType.RESOLVE))
async def on_resolve_click(callback: CallbackQuery, callback_data: AlertAction, session: AsyncSession, user: User, lang: str) -> None:
    try:
        await check_can_resolve(session, callback_data.alert_id, user)
    except ActionError as error:
        await callback.answer(error.render(lang), show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=reasons_keyboard(callback_data.alert_id, lang))
    await callback.answer(t("alert.choose_reason", lang))


@router.callback_query(AlertAction.filter(F.action == AlertActionType.BACK))
async def on_back(callback: CallbackQuery, callback_data: AlertAction, session: AsyncSession, user: User, lang: str) -> None:
    await _refresh_everywhere(callback, session, callback_data.alert_id, user, lang)
    await callback.answer()


async def _refresh_everywhere(callback: CallbackQuery, session: AsyncSession, alert_id: int, user: User, lang: str) -> None:
    """Guruh/shaxsiy yetkazishlarni yangilaydi; tugma /alerts kartasida bosilgan bo'lsa — kartani ham."""
    await refresh_alert_messages(callback.bot, session, alert_id)
    if await find_alert_by_message(session, callback.message.chat.id, callback.message.message_id) is None:
        alert = await session.get(Alert, alert_id, populate_existing=True)
        if alert is not None:
            await render_detail(callback.message, alert, user, lang, edit=True)


@router.callback_query(AlertAction.filter())
async def on_alert_action(
    callback: CallbackQuery, callback_data: AlertAction, session: AsyncSession, user: User, lang: str
) -> None:
    try:
        reply = await apply_action(session, callback_data.alert_id, callback_data.action, user)
    except ActionError as error:
        await callback.answer(error.render(lang), show_alert=True)
        return
    await callback.answer(reply.render(lang))
    if callback_data.action != AlertActionType.MUTE:
        await _refresh_everywhere(callback, session, callback_data.alert_id, user, lang)


@router.callback_query(AlertReason.filter())
async def on_reason(
    callback: CallbackQuery, callback_data: AlertReason, session: AsyncSession, user: User, lang: str
) -> None:
    try:
        reply = await apply_action(
            session, callback_data.alert_id, AlertActionType.RESOLVE, user, reason=callback_data.reason
        )
    except ActionError as error:
        await callback.answer(error.render(lang), show_alert=True)
        return
    await callback.answer(reply.render(lang))
    await _refresh_everywhere(callback, session, callback_data.alert_id, user, lang)


@router.message(StateFilter(None), F.reply_to_message, F.text, ~F.text.startswith("/"))
async def on_alert_reply(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    """Guruhda alert xabariga reply — alertga izoh (privacy mode'da ham bot reply'larni ko'radi)."""
    reply_to = message.reply_to_message
    if reply_to.from_user is None or reply_to.from_user.id != message.bot.id:
        return
    alert_id = await find_alert_by_message(session, message.chat.id, reply_to.message_id)
    if alert_id is None:
        return
    try:
        await add_note(session, alert_id, user, message.text)
    except ActionError as error:
        await message.reply(error.render(lang))
        return
    await refresh_alert_messages(message.bot, session, alert_id)
    with contextlib.suppress(TelegramBadRequest):
        await message.react([ReactionTypeEmoji(emoji="👍")])
