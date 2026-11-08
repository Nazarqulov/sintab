import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.commands import sync_user_commands
from app.bot.keyboards.menu import main_reply_keyboard
from app.bot.keyboards.users import (
    RegistrationDepot,
    name_keyboard,
    phone_keyboard,
    position_keyboard,
    registration_depot_keyboard,
    request_keyboard,
)
from app.bot.notify import send_safe
from app.bot.states import Registration
from app.core.i18n import t
from app.db.models import User, UserStatus
from app.services.depots import list_depots
from app.services.registration import get_or_create_user, is_submitted, list_approvers, submit_registration

logger = logging.getLogger(__name__)
router = Router(name="registration")
router.message.filter(F.chat.type == ChatType.PRIVATE)

PUBLIC = {"public": True}


@router.message(CommandStart(), flags=PUBLIC)
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_or_create_user(session, message.from_user)
    lang = user.language

    if user.status == UserStatus.ACTIVE:
        await sync_user_commands(message.bot, user)
        await message.answer(
            t(
                "reg.welcome_back",
                lang,
                name=escape(user.full_name or message.from_user.full_name),
                role=texts.role_label(user.role, lang),
            ),
            reply_markup=await main_reply_keyboard(user),
        )
        return
    if user.status == UserStatus.BLOCKED:
        await message.answer(t("access.blocked", lang), reply_markup=ReplyKeyboardRemove())
        return
    if is_submitted(user):
        await message.answer(t("access.pending", lang), reply_markup=ReplyKeyboardRemove())
        return

    await state.set_state(Registration.full_name)
    await message.answer(t("reg.intro", lang), reply_markup=name_keyboard(message.from_user.full_name))


@router.message(Command("cancel"), flags=PUBLIC)
async def cmd_cancel(message: Message, state: FSMContext, user: User | None, lang: str) -> None:
    if await state.get_state() is None:
        await message.answer(t("common.nothing_to_cancel", lang))
        return
    await state.clear()
    markup = await main_reply_keyboard(user) if user and user.status == UserStatus.ACTIVE else ReplyKeyboardRemove()
    await message.answer(t("common.cancelled", lang), reply_markup=markup)


@router.message(Registration.full_name, F.text, flags=PUBLIC)
async def reg_full_name(message: Message, state: FSMContext, lang: str) -> None:
    full_name = " ".join(message.text.split())
    if not 3 <= len(full_name) <= 100 or full_name.startswith("/"):
        await message.answer(t("reg.name_invalid", lang))
        return
    await state.update_data(full_name=full_name)
    await state.set_state(Registration.phone)
    await message.answer(t("reg.ask_phone", lang), reply_markup=phone_keyboard(lang))


@router.message(Registration.full_name, flags=PUBLIC)
async def reg_full_name_invalid(message: Message, lang: str) -> None:
    await message.answer(t("reg.name_text_only", lang))


@router.message(Registration.phone, F.contact, flags=PUBLIC)
async def reg_phone(message: Message, state: FSMContext, lang: str) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(t("reg.phone_not_own", lang), reply_markup=phone_keyboard(lang))
        return
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(Registration.position)
    await message.answer(t("reg.ask_position", lang), reply_markup=position_keyboard())


@router.message(Registration.phone, flags=PUBLIC)
async def reg_phone_invalid(message: Message, lang: str) -> None:
    await message.answer(t("reg.phone_use_button", lang, button=t("reg.phone_button", lang)), reply_markup=phone_keyboard(lang))


@router.message(Registration.position, F.text, flags=PUBLIC)
async def reg_position(message: Message, state: FSMContext, session: AsyncSession, user: User | None, lang: str) -> None:
    position = " ".join(message.text.split())
    if not 2 <= len(position) <= 100 or position.startswith("/"):
        await message.answer(t("reg.position_invalid", lang))
        return
    await state.update_data(position=position)

    depots = await list_depots(session)
    if not depots:
        await _finish(message, state, session, user, lang, depot_id=None)
        return
    await state.set_state(Registration.depot)
    await message.answer(t("reg.thanks", lang), reply_markup=ReplyKeyboardRemove())
    await message.answer(t("reg.ask_depot", lang), reply_markup=registration_depot_keyboard(depots, lang))


@router.message(Registration.position, flags=PUBLIC)
async def reg_position_invalid(message: Message, lang: str) -> None:
    await message.answer(t("reg.position_text_only", lang), reply_markup=position_keyboard())


@router.message(Registration.depot, flags=PUBLIC)
async def reg_depot_hint(message: Message, lang: str) -> None:
    await message.answer(t("reg.depot_pick_hint", lang))


@router.callback_query(Registration.depot, RegistrationDepot.filter(), flags=PUBLIC)
async def reg_depot(
    callback: CallbackQuery,
    callback_data: RegistrationDepot,
    state: FSMContext,
    session: AsyncSession,
    user: User | None,
    lang: str,
) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _finish(callback.message, state, session, user, lang, depot_id=callback_data.depot_id or None)


async def _finish(
    message: Message, state: FSMContext, session: AsyncSession, user: User | None, lang: str, depot_id: int | None
) -> None:
    data = await state.get_data()
    await state.clear()
    if user is None or user.status != UserStatus.PENDING:
        await message.answer(t("access.not_registered", lang), reply_markup=ReplyKeyboardRemove())
        return
    user = await submit_registration(
        session,
        user,
        full_name=data["full_name"],
        phone=data["phone"],
        position=data["position"],
        depot_id=depot_id,
    )
    await message.answer(t("reg.submitted", lang), reply_markup=ReplyKeyboardRemove())
    await notify_approvers(message.bot, session, user)


async def notify_approvers(bot: Bot, session: AsyncSession, user: User) -> None:
    delivered = 0
    for approver in await list_approvers(session):
        lang = approver.language
        if await send_safe(bot, approver.telegram_id, texts.registration_request(user, lang), request_keyboard(user.id, lang)):
            delivered += 1
    if not delivered:
        logger.error("Foydalanuvchi %s so'rovi hech bir adminga yetib bormadi", user.id)
