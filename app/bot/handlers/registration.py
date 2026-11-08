import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
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

    if user.status == UserStatus.ACTIVE:
        await message.answer(
            f"👋 Xush kelibsiz, <b>{escape(user.full_name or message.from_user.full_name)}</b>!\n"
            f"Rolingiz: {texts.ROLE_LABELS[user.role]}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if user.status == UserStatus.BLOCKED:
        await message.answer(texts.BLOCKED, reply_markup=ReplyKeyboardRemove())
        return
    if is_submitted(user):
        await message.answer(texts.PENDING, reply_markup=ReplyKeyboardRemove())
        return

    await state.set_state(Registration.full_name)
    await message.answer(
        "Ro'yxatdan o'tish — 4 ta savol. Istalgan payt /cancel bilan bekor qilish mumkin.\n\n"
        "<b>1/4.</b> Ism-familiyangizni yozing:",
        reply_markup=name_keyboard(message.from_user.full_name),
    )


@router.message(Command("cancel"), flags=PUBLIC)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Bekor qilinadigan amal yo'q.")
        return
    await state.clear()
    await message.answer("❌ Bekor qilindi.", reply_markup=ReplyKeyboardRemove())


@router.message(Registration.full_name, F.text, flags=PUBLIC)
async def reg_full_name(message: Message, state: FSMContext) -> None:
    full_name = " ".join(message.text.split())
    if not 3 <= len(full_name) <= 100 or full_name.startswith("/"):
        await message.answer("Ism-familiya 3–100 belgidan iborat bo'lsin. Qaytadan yozing:")
        return
    await state.update_data(full_name=full_name)
    await state.set_state(Registration.phone)
    await message.answer("<b>2/4.</b> Telefon raqamingizni pastdagi tugma orqali yuboring:", reply_markup=phone_keyboard())


@router.message(Registration.full_name, flags=PUBLIC)
async def reg_full_name_invalid(message: Message) -> None:
    await message.answer("Ism-familiyangizni matn ko'rinishida yozing:")


@router.message(Registration.phone, F.contact, flags=PUBLIC)
async def reg_phone(message: Message, state: FSMContext) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer("Faqat o'zingizning raqamingizni yuboring — pastdagi tugmani bosing.", reply_markup=phone_keyboard())
        return
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(Registration.position)
    await message.answer("<b>3/4.</b> Lavozimingiz (tanlang yoki yozing):", reply_markup=position_keyboard())


@router.message(Registration.phone, flags=PUBLIC)
async def reg_phone_invalid(message: Message) -> None:
    await message.answer("Iltimos, «📱 Raqamimni yuborish» tugmasini bosing.", reply_markup=phone_keyboard())


@router.message(Registration.position, F.text, flags=PUBLIC)
async def reg_position(message: Message, state: FSMContext, session: AsyncSession, user: User | None) -> None:
    position = " ".join(message.text.split())
    if not 2 <= len(position) <= 100 or position.startswith("/"):
        await message.answer("Lavozim 2–100 belgidan iborat bo'lsin. Qaytadan yozing:")
        return
    await state.update_data(position=position)

    depots = await list_depots(session)
    if not depots:
        await _finish(message, state, session, user, depot_id=None)
        return
    await state.set_state(Registration.depot)
    await message.answer("Rahmat.", reply_markup=ReplyKeyboardRemove())
    await message.answer("<b>4/4.</b> Qaysi depoda ishlaysiz?", reply_markup=registration_depot_keyboard(depots))


@router.message(Registration.position, flags=PUBLIC)
async def reg_position_invalid(message: Message) -> None:
    await message.answer("Lavozimingizni matn ko'rinishida yozing:", reply_markup=position_keyboard())


@router.message(Registration.depot, flags=PUBLIC)
async def reg_depot_hint(message: Message) -> None:
    await message.answer("Yuqoridagi ro'yxatdan depoingizni tanlang.")


@router.callback_query(Registration.depot, RegistrationDepot.filter(), flags=PUBLIC)
async def reg_depot(
    callback: CallbackQuery,
    callback_data: RegistrationDepot,
    state: FSMContext,
    session: AsyncSession,
    user: User | None,
) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _finish(callback.message, state, session, user, depot_id=callback_data.depot_id or None)


async def _finish(
    message: Message, state: FSMContext, session: AsyncSession, user: User | None, depot_id: int | None
) -> None:
    data = await state.get_data()
    await state.clear()
    if user is None or user.status != UserStatus.PENDING:
        await message.answer(texts.NOT_REGISTERED, reply_markup=ReplyKeyboardRemove())
        return
    user = await submit_registration(
        session,
        user,
        full_name=data["full_name"],
        phone=data["phone"],
        position=data["position"],
        depot_id=depot_id,
    )
    await message.answer(
        "✅ So'rovingiz yuborildi! Administrator tasdiqlagach shu yerga xabar keladi.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await notify_approvers(message.bot, session, user)


async def notify_approvers(bot: Bot, session: AsyncSession, user: User) -> None:
    approvers = await list_approvers(session)
    delivered = 0
    for approver in approvers:
        if await send_safe(bot, approver.telegram_id, texts.registration_request(user), request_keyboard(user.id)):
            delivered += 1
    if not delivered:
        logger.error("Foydalanuvchi %s so'rovi hech bir adminga yetib bormadi", user.id)
