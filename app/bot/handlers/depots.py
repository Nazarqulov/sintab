from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import DepotCreation
from app.core.permissions import Permission
from app.db.models import User
from app.services.depots import DepotError, create_depot, delete_depot, list_depots

router = Router(name="depots")

SETTINGS = {"permission": Permission.MANAGE_SETTINGS}


class DepotAdmin(CallbackData, prefix="dp"):
    action: str
    depot_id: int = 0


def _btn(text: str, action: str, depot_id: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=DepotAdmin(action=action, depot_id=depot_id).pack())


async def _list_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    depots = await list_depots(session)
    lines = ["🏢 <b>Depolar</b>", ""]
    lines += [f"• {escape(d.name)}" for d in depots] or ["Hali depo yo'q."]
    rows = [[_btn(f"🗑 {d.name}", "del", d.id)] for d in depots]
    rows.append([_btn("➕ Depo qo'shish", "add")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("depots"), flags=SETTINGS)
async def cmd_depots(message: Message, session: AsyncSession) -> None:
    text, markup = await _list_view(session)
    await message.answer(text, reply_markup=markup)


@router.callback_query(DepotAdmin.filter(F.action == "list"), flags=SETTINGS)
async def cb_list(callback: CallbackQuery, session: AsyncSession) -> None:
    text, markup = await _list_view(session)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(DepotAdmin.filter(F.action == "add"), flags=SETTINGS)
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DepotCreation.name)
    await callback.message.answer("Yangi depo nomini yozing (bekor qilish — /cancel):")
    await callback.answer()


@router.message(DepotCreation.name, F.text, ~F.text.startswith("/"), flags=SETTINGS)
async def on_depot_name(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        depot = await create_depot(session, user, message.text)
    except DepotError as error:
        await message.answer(f"{error} Qaytadan yozing yoki /cancel:")
        return
    await state.clear()
    text, markup = await _list_view(session)
    await message.answer(f"✅ «{escape(depot.name)}» qo'shildi.\n\n{text}", reply_markup=markup)


@router.callback_query(DepotAdmin.filter(F.action == "del"), flags=SETTINGS)
async def cb_delete_confirm(callback: CallbackQuery, callback_data: DepotAdmin) -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[_btn("🗑 Ha, o'chirilsin", "delok", callback_data.depot_id), _btn("⬅️ Yo'q", "list")]]
    )
    await callback.message.edit_text(
        "⚠️ Depo o'chirilsinmi? Foydalanuvchilarning bu depoga biriktirilishi o'chadi, "
        "mashina, guruh va routing qoidalarida depo bo'sh qoladi.",
        reply_markup=markup,
    )
    await callback.answer()


@router.callback_query(DepotAdmin.filter(F.action == "delok"), flags=SETTINGS)
async def cb_delete(callback: CallbackQuery, callback_data: DepotAdmin, session: AsyncSession, user: User) -> None:
    try:
        name = await delete_depot(session, user, callback_data.depot_id)
    except DepotError as error:
        await callback.answer(str(error), show_alert=True)
        return
    text, markup = await _list_view(session)
    await callback.message.edit_text(f"🗑 «{escape(name)}» o'chirildi.\n\n{text}", reply_markup=markup)
    await callback.answer()
