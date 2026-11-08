"""/chat: foydalanuvchilar orasida ichki yozishma (matn, fayl, rasm, ovoz...) — bot orqali relay."""

import contextlib
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReactionTypeEmoji
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.menu import home_button
from app.bot.states import ChatFlow
from app.core.i18n import t
from app.db.models import User
from app.services import chat as svc
from app.services.users import display_name

logger = logging.getLogger(__name__)
router = Router(name="chat")
router.message.filter(F.chat.type == ChatType.PRIVATE)

PAGE_SIZE = 10
TEXT_LIMIT = 4000
CAPTION_LIMIT = 1000
CAPTION_TYPES = ("photo", "document", "video", "audio", "voice", "animation")


class ChatNav(CallbackData, prefix="ch"):
    action: str  # list | search | to | end
    uid: int = 0
    page: int = 0


def _btn(text: str, action: str, **kw) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=ChatNav(action=action, **kw).pack())


def write_button(user_id: int, lang: str | None) -> InlineKeyboardButton:
    return _btn(t("chat.write", lang), "to", uid=user_id)


async def contacts_view(session: AsyncSession, user: User, lang: str, page: int = 0, query: str | None = None):
    users = await svc.contacts(session, user, query)
    pages = max((len(users) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 0), pages - 1)
    rows = [
        [_btn(f"{display_name(u)} · {t(f'role.{u.role.value}', lang)}", "to", uid=u.id)]
        for u in users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    ]
    nav = []
    if page > 0:
        nav.append(_btn("◀️", "list", page=page - 1))
    if page < pages - 1:
        nav.append(_btn("▶️", "list", page=page + 1))
    rows += [nav, [_btn(t("browse.search", lang), "search")], [home_button(lang)]]
    text = t("chat.title", lang, count=len(users)) + ("" if users else "\n\n" + t("chat.no_contacts", lang))
    return text, InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def show_chat(target: Message, session: AsyncSession, user: User, lang: str) -> None:
    text, markup = await contacts_view(session, user, lang)
    await target.answer(text, reply_markup=markup)


@router.message(Command("chat"))
async def cmd_chat(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await show_chat(message, session, user, lang)


async def start_compose(target: Message, session: AsyncSession, user: User, lang: str, state: FSMContext, recipient_id: int) -> bool:
    recipient = await session.get(User, recipient_id)
    if recipient is None or not await svc.can_chat(session, user, recipient):
        return False
    await state.set_state(ChatFlow.compose)
    await state.update_data(chat_to=recipient.id)
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("chat.end", lang), "end")]])
    await target.answer(t("chat.compose", lang, name=escape(display_name(recipient))), reply_markup=markup)
    return True


@router.callback_query(ChatNav.filter())
async def cb_chat(callback: CallbackQuery, callback_data: ChatNav, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    action, message = callback_data.action, callback.message
    if action == "list":
        text, markup = await contacts_view(session, user, lang, callback_data.page)
        await message.edit_text(text, reply_markup=markup)
    elif action == "search":
        await state.set_state(ChatFlow.search)
        await message.answer(t("chat.ask_query", lang))
    elif action == "to":
        if not await start_compose(message, session, user, lang, state, callback_data.uid):
            await callback.answer(t("chat.unavailable", lang), show_alert=True)
            return
    elif action == "end":
        await state.clear()
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_text(t("chat.ended", lang))
    await callback.answer()


@router.message(ChatFlow.search, F.text, ~F.text.startswith("/"))
async def on_search(message: Message, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    await state.clear()
    text, markup = await contacts_view(session, user, lang, query=message.text)
    await message.answer(text, reply_markup=markup)


# --- Relay ---

async def relay(bot: Bot, message: Message, sender: User, recipient: User) -> int | None:
    """Xabarni qabul qiluvchiga yuboruvchi nomi va "Javob" tugmasi bilan yetkazadi; yetkazilgan message_id."""
    lang = recipient.language
    header = t("chat.from", lang, name=escape(display_name(sender)), role=t(f"role.{sender.role.value}", lang))
    markup = InlineKeyboardMarkup(inline_keyboard=[[write_button(sender.id, lang)]])
    chat_id = recipient.telegram_id
    try:
        if message.text is not None and len(message.html_text) + len(header) < TEXT_LIMIT:
            sent = await bot.send_message(chat_id, f"{header}\n\n{message.html_text}", reply_markup=markup)
            return sent.message_id
        caption = f"{header}\n\n{message.html_text}" if message.caption else header
        if message.content_type in CAPTION_TYPES and len(caption) < CAPTION_LIMIT:
            copied = await bot.copy_message(chat_id, message.chat.id, message.message_id, caption=caption, reply_markup=markup)
            return copied.message_id
        # Stiker, joylashuv, kontakt, uzun matn — sarlavha alohida, keyin asl xabar nusxasi.
        await bot.send_message(chat_id, header)
        copied = await bot.copy_message(chat_id, message.chat.id, message.message_id, reply_markup=markup)
        return copied.message_id
    except TelegramAPIError as error:
        logger.warning("Chat xabari %s -> %s yetkazilmadi: %s", sender.id, recipient.id, error)
        return None


async def _deliver(message: Message, session: AsyncSession, user: User, lang: str, recipient: User) -> None:
    if not await svc.can_chat(session, user, recipient):
        await message.answer(t("chat.unavailable", lang))
        return
    delivered = await relay(message.bot, message, user, recipient)
    if delivered is None:
        await message.answer(t("chat.not_delivered", lang, name=escape(display_name(recipient))))
        return
    await svc.log_message(session, user, recipient, message, delivered)
    try:
        await message.react([ReactionTypeEmoji(emoji="👌")])
    except TelegramBadRequest:
        await message.answer(t("chat.sent", lang))


def _replied_to_bot(message: Message) -> bool:
    reply_to = message.reply_to_message
    return reply_to is not None and reply_to.from_user is not None and reply_to.from_user.id == message.bot.id


@router.message(ChatFlow.compose, ~F.text.startswith("/"))
async def on_compose(message: Message, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    recipient = None
    if _replied_to_bot(message):
        recipient = await svc.find_reply_target(session, user, message.reply_to_message.message_id)
    if recipient is None:
        recipient = await session.get(User, (await state.get_data()).get("chat_to") or 0)
    if recipient is None:
        await state.clear()
        await message.answer(t("chat.unavailable", lang))
        return
    await _deliver(message, session, user, lang, recipient)


@router.message(StateFilter(None), F.reply_to_message, ~F.text.startswith("/"))
async def on_reply(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    """Yetkazilgan xabarga oddiy Telegram reply — suhbat rejimisiz ham javob ketadi."""
    recipient = await svc.find_reply_target(session, user, message.reply_to_message.message_id) if _replied_to_bot(message) else None
    if recipient is None:
        raise SkipHandler  # chat xabari emas — boshqa handler'lar (masalan alert izohi) ko'rsin
    await _deliver(message, session, user, lang, recipient)
