"""/broadcast: istalgan xabar (matn, rasm, PDF, Excel...) → auditoriya tanlash → tasdiq → nusxalab yuborish."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import BroadcastFlow
from app.bot.texts import role_label
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import User, UserRole
from app.services.broadcast import audience, send_broadcast
from app.services.depots import list_depots

router = Router(name="broadcast")

BROADCAST = {"permission": Permission.SEND_BROADCAST}
ROLES = [UserRole.ADMIN, UserRole.DISPATCHER, UserRole.MANAGER, UserRole.VIEWER, UserRole.DRIVER]


class BroadcastAction(CallbackData, prefix="bc"):
    action: str
    target: str | None = None


def _btn(text: str, action: str, target: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=BroadcastAction(action=action, target=target).pack())


async def start_broadcast(target: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(BroadcastFlow.message)
    await target.answer(t("broadcast.ask", lang))


@router.message(Command("broadcast"), flags=BROADCAST)
async def cmd_broadcast(message: Message, state: FSMContext, lang: str) -> None:
    await start_broadcast(message, state, lang)


@router.message(BroadcastFlow.message, ~F.text.startswith("/"), flags=BROADCAST)
async def on_broadcast_message(message: Message, state: FSMContext, session: AsyncSession, lang: str) -> None:
    await state.update_data(broadcast_message_id=message.message_id, broadcast_chat_id=message.chat.id)
    await state.set_state(None)
    rows = [[_btn(t("broadcast.all", lang), "pick", "all")]]
    rows += [[_btn(role_label(role, lang), "pick", f"role-{role.value}")] for role in ROLES]
    rows += [[_btn(f"🏢 {d.name}", "pick", f"depot-{d.id}")] for d in await list_depots(session)]
    rows.append([_btn(t("common.cancel", lang), "cancel")])
    await message.answer(t("broadcast.pick_audience", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(BroadcastAction.filter(F.action == "pick"), flags=BROADCAST)
async def cb_pick(callback: CallbackQuery, callback_data: BroadcastAction, session: AsyncSession, user: User, lang: str) -> None:
    try:
        recipients = [u for u in await audience(session, callback_data.target or "") if u.id != user.id]
    except ValueError:
        await callback.answer(t("settings.invalid", lang), show_alert=True)
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        _btn(t("broadcast.send", lang, count=len(recipients)), "send", callback_data.target),
        _btn(t("common.no", lang), "cancel"),
    ]])
    await callback.message.edit_text(t("broadcast.confirm", lang, count=len(recipients)), reply_markup=markup)
    await callback.answer()


@router.callback_query(BroadcastAction.filter(F.action == "send"), flags=BROADCAST)
async def cb_send(
    callback: CallbackQuery, callback_data: BroadcastAction, session: AsyncSession, user: User, lang: str, state: FSMContext
) -> None:
    data = await state.get_data()
    if "broadcast_message_id" not in data:
        await callback.answer(t("broadcast.expired", lang), show_alert=True)
        return
    await state.update_data(broadcast_message_id=None, broadcast_chat_id=None)
    await callback.message.edit_text(t("broadcast.sending", lang))
    await callback.answer()
    result = await send_broadcast(
        session,
        callback.bot,
        user,
        await audience(session, callback_data.target or "all"),
        data["broadcast_chat_id"],
        data["broadcast_message_id"],
    )
    await callback.message.edit_text(t("broadcast.done", lang, sent=result.sent, failed=result.failed))


@router.callback_query(BroadcastAction.filter(F.action == "cancel"), flags=BROADCAST)
async def cb_cancel(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await callback.message.edit_text(t("common.cancelled", lang))
    await callback.answer()
