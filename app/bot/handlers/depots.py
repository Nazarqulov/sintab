from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.menu import home_button
from app.bot.states import DepotCreation
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import User
from app.services.depots import (
    DepotError,
    create_depot,
    delete_depot,
    distinct_tags,
    list_depots,
    map_tag_to_depot,
    tag_depot_map,
)

router = Router(name="depots")

SETTINGS = {"permission": Permission.MANAGE_SETTINGS}
MAX_TAG_BUTTONS = 30


class DepotAdmin(CallbackData, prefix="dp"):
    action: str
    depot_id: int = 0
    # Tag'lar ixtiyoriy matn — callback'ga sig'masligi mumkin, shuning uchun ro'yxatdagi indeksi.
    tag_index: int = -1


def _btn(text: str, action: str, depot_id: int = 0, tag_index: int = -1) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=DepotAdmin(action=action, depot_id=depot_id, tag_index=tag_index).pack()
    )


async def list_view(session: AsyncSession, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    depots = await list_depots(session)
    mapping = await tag_depot_map(session)
    names = {d.id: d.name for d in depots}
    lines = [t("depots.title", lang), ""]
    lines += [f"• {escape(d.name)}" for d in depots] or [t("depots.empty", lang)]
    if mapping:
        lines += ["", t("depots.tag_rules", lang)]
        lines += [f"🏷 {escape(tag)} → {escape(names.get(did, '?'))}" for tag, did in sorted(mapping.items())]
    rows = [[_btn(f"🗑 {d.name}", "del", d.id)] for d in depots]
    rows.append([_btn(t("depots.add", lang), "add")])
    if depots:
        rows.append([_btn(t("depots.map_tag", lang), "tags")])
    rows.append([home_button(lang)])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def show_depots(target: Message, session: AsyncSession, lang: str) -> None:
    text, markup = await list_view(session, lang)
    await target.answer(text, reply_markup=markup)


@router.message(Command("depots"), flags=SETTINGS)
async def cmd_depots(message: Message, session: AsyncSession, lang: str) -> None:
    await show_depots(message, session, lang)


@router.callback_query(DepotAdmin.filter(F.action == "list"), flags=SETTINGS)
async def cb_list(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    text, markup = await list_view(session, lang)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(DepotAdmin.filter(F.action == "add"), flags=SETTINGS)
async def cb_add(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.set_state(DepotCreation.name)
    await callback.message.answer(t("depots.ask_name", lang))
    await callback.answer()


@router.message(DepotCreation.name, F.text, ~F.text.startswith("/"), flags=SETTINGS)
async def on_depot_name(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str) -> None:
    try:
        depot = await create_depot(session, user, message.text)
    except DepotError as error:
        await message.answer(t("depots.retry", lang, error=error.render(lang)))
        return
    await state.clear()
    text, markup = await list_view(session, lang)
    await message.answer(f"{t('depots.added', lang, name=escape(depot.name))}\n\n{text}", reply_markup=markup)


@router.callback_query(DepotAdmin.filter(F.action == "del"), flags=SETTINGS)
async def cb_delete_confirm(callback: CallbackQuery, callback_data: DepotAdmin, lang: str) -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[
            _btn(t("common.yes_delete", lang), "delok", callback_data.depot_id),
            _btn(t("common.no", lang), "list"),
        ]]
    )
    await callback.message.edit_text(t("depots.confirm_delete", lang), reply_markup=markup)
    await callback.answer()


@router.callback_query(DepotAdmin.filter(F.action == "delok"), flags=SETTINGS)
async def cb_delete(
    callback: CallbackQuery, callback_data: DepotAdmin, session: AsyncSession, user: User, lang: str
) -> None:
    try:
        name = await delete_depot(session, user, callback_data.depot_id)
    except DepotError as error:
        await callback.answer(error.render(lang), show_alert=True)
        return
    text, markup = await list_view(session, lang)
    await callback.message.edit_text(f"{t('depots.deleted', lang, name=escape(name))}\n\n{text}", reply_markup=markup)
    await callback.answer()


@router.callback_query(DepotAdmin.filter(F.action == "tags"), flags=SETTINGS)
async def cb_tags(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    tags = (await distinct_tags(session))[:MAX_TAG_BUTTONS]
    if not tags:
        await callback.answer(t("depots.no_tags", lang), show_alert=True)
        return
    rows = [[_btn(f"🏷 {tag}", "tag", tag_index=i)] for i, tag in enumerate(tags)]
    rows.append([_btn(t("common.back", lang), "list")])
    await callback.message.edit_text(t("depots.pick_tag", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(DepotAdmin.filter(F.action == "tag"), flags=SETTINGS)
async def cb_tag_pick_depot(callback: CallbackQuery, callback_data: DepotAdmin, session: AsyncSession, lang: str) -> None:
    tags = await distinct_tags(session)
    if not 0 <= callback_data.tag_index < len(tags):
        await callback.answer(t("depots.no_tags", lang), show_alert=True)
        return
    rows = [[_btn(f"🏢 {d.name}", "tagset", d.id, callback_data.tag_index)] for d in await list_depots(session)]
    rows.append([_btn(t("common.back", lang), "tags")])
    await callback.message.edit_text(
        t("depots.pick_depot_for_tag", lang, tag=escape(tags[callback_data.tag_index])),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(DepotAdmin.filter(F.action == "tagset"), flags=SETTINGS)
async def cb_tag_set(
    callback: CallbackQuery, callback_data: DepotAdmin, session: AsyncSession, user: User, lang: str
) -> None:
    tags = await distinct_tags(session)
    if not 0 <= callback_data.tag_index < len(tags):
        await callback.answer(t("depots.no_tags", lang), show_alert=True)
        return
    tag = tags[callback_data.tag_index]
    try:
        count = await map_tag_to_depot(session, user, tag, callback_data.depot_id)
    except DepotError as error:
        await callback.answer(error.render(lang), show_alert=True)
        return
    text, markup = await list_view(session, lang)
    await callback.message.edit_text(
        f"{t('depots.tag_mapped', lang, tag=escape(tag), count=count)}\n\n{text}", reply_markup=markup
    )
    await callback.answer()
