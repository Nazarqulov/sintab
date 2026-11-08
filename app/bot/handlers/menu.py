"""/menu (inline) va pastdagi doimiy tugmalar — har bo'lim o'z handler'idagi `show_*` funksiyasiga yo'naltiriladi."""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import admin, browse, chat, documents, fleet, personal, reports
from app.bot.keyboards.menu import MenuAction, allowed_sections, main_inline_menu
from app.core.i18n import t, variants
from app.db.models import User

router = Router(name="menu")
router.message.filter(F.chat.type == ChatType.PRIVATE)


async def open_section(section: str, target: Message, session: AsyncSession, user: User, lang: str) -> bool:
    if section not in await allowed_sections(user) and section != "home":
        return False
    if section == "home":
        await target.answer(t("menu.title", lang), reply_markup=await main_inline_menu(user))
    elif section == "alerts":
        await browse.show_alerts(target, session, user, lang)
    elif section == "vehicles":
        await browse.show_vehicles(target, session, user, lang)
    elif section == "drivers":
        await browse.show_drivers(target, session, user, lang)
    elif section == "hos":
        await fleet.show_hos(target, session, user, lang)
    elif section == "fuel":
        await fleet.show_fuel(target, session, user, lang)
    elif section == "docs":
        await documents.show_docs(target, user, lang)
    elif section == "chat":
        await chat.show_chat(target, session, user, lang)
    elif section == "reports":
        await reports.show_reports(target, session, user, lang)
    elif section == "settings":
        await personal.show_settings(target, user, lang)
    elif section == "profile":
        await personal.show_profile(target, user, lang)
    elif section == "admin":
        await admin.show_admin(target, user, lang)
    else:
        return False
    return True


@router.message(Command("menu"))
async def cmd_menu(message: Message, user: User, lang: str) -> None:
    await message.answer(t("menu.title", lang), reply_markup=await main_inline_menu(user))


@router.callback_query(MenuAction.filter())
async def cb_menu(callback: CallbackQuery, callback_data: MenuAction, session: AsyncSession, user: User, lang: str) -> None:
    if not await open_section(callback_data.section, callback.message, session, user, lang):
        await callback.answer(t("access.no_permission", lang), show_alert=True)
        return
    await callback.answer()


REPLY_BUTTONS = {text: key for key in ("alerts", "vehicles", "drivers", "hos", "fuel", "docs", "chat", "reports", "settings", "profile", "admin") for text in variants(f"menu.{key}")}
REPLY_BUTTONS.update({text: "home" for text in variants("menu.menu")})


# Har qanday holatda: pastdagi tugma bosilsa joriy oqim (qidiruv, chat...) tugaydi — tugma matni
# chat suhbatdoshiga yoki qidiruvga ketib qolmasin.
@router.message(F.text.in_(REPLY_BUTTONS))
async def on_reply_button(message: Message, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    await state.clear()
    if not await open_section(REPLY_BUTTONS[message.text], message, session, user, lang):
        await message.answer(t("access.no_permission", lang))
