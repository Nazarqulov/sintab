"""/docs: hujjatlarni ko'rish, yuklash (PDF, Excel, Word, rasm), yuklab olish va muddat."""

from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.menu import home_button
from app.bot.states import DocFlow
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import Document, DocumentOwner, Driver, User, UserRole, Vehicle
from app.services import documents as svc
from app.services.alerts.formatter import format_time, safe_zone
from app.services.documents import DocumentError
from app.services.scope import scoped_drivers, scoped_vehicles
from app.services.users import display_name, has_permission

router = Router(name="documents")

MAX_SEARCH = 15


class DocNav(CallbackData, prefix="dc"):
    action: str
    owner: str | None = None
    owner_id: int = 0
    page: int = 0
    doc_id: int = 0
    arg: str | None = None


def _btn(text: str, action: str, **kw) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=DocNav(action=action, **kw).pack())


async def _allowed(user: User) -> bool:
    return user.role == UserRole.DRIVER or await has_permission(user, Permission.VIEW_VEHICLES)


def _doc_label(document: Document, lang: str) -> str:
    expiry = f" · ⏰ {document.expires_at:%d.%m.%Y}" if document.expires_at else ""
    return f"📄 {document.title} · {t(f'docs.type.{document.doc_type}', lang)}{expiry}"[:64]


# --- Bosh sahifa ---

async def docs_home(user: User, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    if user.role == UserRole.DRIVER:
        rows = [
            [_btn(t("docs.my_docs", lang), "list", owner=DocumentOwner.DRIVER.value, owner_id=user.driver_id or 0)],
            [_btn(t("docs.upload", lang), "add", owner=DocumentOwner.DRIVER.value, owner_id=user.driver_id or 0)],
        ]
    else:
        rows = [
            [_btn(t("docs.company", lang), "list", owner=DocumentOwner.COMPANY.value)],
            [_btn(t("docs.drivers", lang), "pick", owner=DocumentOwner.DRIVER.value),
             _btn(t("docs.vehicles", lang), "pick", owner=DocumentOwner.VEHICLE.value)],
            [_btn(t("docs.expiring", lang), "expiring")],
            [_btn(t("docs.upload", lang), "add")],
        ]
    rows.append([home_button(lang)])
    return t("docs.title", lang), InlineKeyboardMarkup(inline_keyboard=rows)


async def show_docs(target: Message, user: User, lang: str) -> None:
    text, markup = await docs_home(user, lang)
    await target.answer(text, reply_markup=markup)


@router.message(Command("docs"))
async def cmd_docs(message: Message, user: User, lang: str) -> None:
    if not await _allowed(user):
        await message.answer(t("access.no_permission", lang))
        return
    await show_docs(message, user, lang)


# --- Ro'yxat va yuklab olish ---

async def list_view(session: AsyncSession, user: User, lang: str, owner: str | None, owner_id: int, page: int):
    documents, pages = await svc.list_documents(session, user, owner, owner_id or None, page)
    title = t("docs.list_title", lang, owner=escape(await svc.owner_label(session, owner, owner_id or None)) if owner else "")
    rows = [[_btn(_doc_label(d, lang), "get", doc_id=d.id)] for d in documents]
    nav = []
    if page > 0:
        nav.append(_btn("◀️", "list", owner=owner, owner_id=owner_id, page=page - 1))
    if page < pages - 1:
        nav.append(_btn("▶️", "list", owner=owner, owner_id=owner_id, page=page + 1))
    rows += [nav, [_btn(t("docs.add_here", lang), "add", owner=owner, owner_id=owner_id)], [_btn(t("common.back", lang), "home")]]
    text = title + ("" if documents else "\n\n" + t("docs.empty", lang))
    return text, InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def send_document_file(target: Message, session: AsyncSession, document: Document, user: User, lang: str) -> None:
    caption = t(
        "docs.caption", lang,
        title=escape(document.title),
        type=t(f"docs.type.{document.doc_type}", lang),
        owner=escape(await svc.owner_label(session, document.owner_type, document.owner_id)),
        expires=document.expires_at.strftime("%d.%m.%Y") if document.expires_at else "—",
        uploader=escape(display_name(document.uploader)),
        uploaded=format_time(document.created_at, user.timezone),
    )
    markup = None
    if document.uploaded_by == user.id or await has_permission(user, Permission.MANAGE_SETTINGS):
        markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("docs.delete", lang), "del", doc_id=document.id)]])
    # Rasm sifatida yuborilgan (fayl nomisiz) — rasm, qolgani — hujjat.
    if document.file_name is None and (document.mime_type or "").startswith("image/"):
        await target.answer_photo(document.file_id, caption=caption, reply_markup=markup)
    else:
        await target.answer_document(document.file_id, caption=caption, reply_markup=markup)


# --- Callback'lar ---

@router.callback_query(DocNav.filter())
async def cb_docs(callback: CallbackQuery, callback_data: DocNav, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    if not await _allowed(user):
        await callback.answer(t("access.no_permission", lang), show_alert=True)
        return
    action, message = callback_data.action, callback.message
    try:
        if action == "home":
            text, markup = await docs_home(user, lang)
            await message.edit_text(text, reply_markup=markup)
        elif action == "list":
            if callback_data.owner and callback_data.owner != DocumentOwner.COMPANY.value and not await svc.can_access(
                session, user, callback_data.owner, callback_data.owner_id or None
            ):
                raise DocumentError("access.no_permission")
            text, markup = await list_view(session, user, lang, callback_data.owner, callback_data.owner_id, callback_data.page)
            await message.edit_text(text, reply_markup=markup)
        elif action == "expiring":
            today = datetime.now(UTC).astimezone(safe_zone(user.timezone)[0]).date()
            documents = await svc.expiring_documents(session, user, today)
            rows = [[_btn(_doc_label(d, lang), "get", doc_id=d.id)] for d in documents[:30]]
            rows.append([_btn(t("common.back", lang), "home")])
            text = t("docs.expiring_title", lang, count=len(documents))
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        elif action == "get":
            document = await svc.get_document(session, user, callback_data.doc_id)
            await send_document_file(message, session, document, user, lang)
        elif action == "del":
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                _btn(t("common.yes_delete", lang), "delok", doc_id=callback_data.doc_id), _btn(t("common.no", lang), "noop"),
            ]])
            await message.answer(t("docs.confirm_delete", lang), reply_markup=markup)
        elif action == "delok":
            await svc.delete_document(session, user, callback_data.doc_id)
            await message.edit_text(t("docs.deleted", lang))
        elif action == "noop":
            await message.delete()
        elif action == "pick":
            await state.set_state(DocFlow.search)
            await state.update_data(doc_owner=callback_data.owner, doc_intent="list")
            await message.answer(t("docs.ask_owner_search", lang))
        elif action == "add":
            await _start_upload(message, session, user, lang, state, callback_data.owner, callback_data.owner_id)
        elif action == "owner":
            await state.update_data(doc_owner=callback_data.owner)
            await state.set_state(DocFlow.search)
            await state.update_data(doc_intent="add")
            await message.answer(t("docs.ask_owner_search", lang))
        elif action == "type":
            data = await state.get_data()
            await state.update_data(doc_type=callback_data.arg)
            await state.set_state(DocFlow.file)
            owner = await svc.owner_label(session, data.get("doc_owner"), data.get("doc_owner_id"))
            await message.edit_text(t("docs.ask_file", lang, type=t(f"docs.type.{callback_data.arg}", lang), owner=escape(owner)))
        elif action == "noexpiry":
            await _save(message, session, user, lang, state, None)
    except DocumentError as error:
        await callback.answer(error.render(lang), show_alert=True)
        return
    await callback.answer()


async def _start_upload(target: Message, session: AsyncSession, user: User, lang: str, state: FSMContext, owner: str | None, owner_id: int) -> None:
    await state.set_state(None)
    await state.update_data(doc_owner=owner, doc_owner_id=owner_id or None)
    if user.role == UserRole.DRIVER:
        owner, owner_id = DocumentOwner.DRIVER.value, user.driver_id or 0
        if not owner_id:
            raise DocumentError("fleet.driver_not_linked")
        await state.update_data(doc_owner=owner, doc_owner_id=owner_id)
    if owner is None:
        rows = [[_btn(t(f"docs.owner.{o.value}", lang), "add" if o == DocumentOwner.COMPANY else "owner", owner=o.value)]
                for o in (DocumentOwner.COMPANY, DocumentOwner.DRIVER, DocumentOwner.VEHICLE)]
        await target.answer(t("docs.ask_owner_type", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    if owner in (DocumentOwner.DRIVER.value, DocumentOwner.VEHICLE.value, DocumentOwner.ALERT.value) and not owner_id:
        await state.set_state(DocFlow.search)
        await state.update_data(doc_intent="add")
        await target.answer(t("docs.ask_owner_search", lang))
        return
    if not await svc.can_access(session, user, owner, owner_id or None):
        raise DocumentError("access.no_permission")
    types = svc.OWNER_DOC_TYPES[DocumentOwner(owner)]
    rows = [[_btn(t(f"docs.type.{d.value}", lang), "type", arg=d.value)] for d in types]
    await target.answer(t("docs.ask_type", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# --- FSM: egasini qidirish, fayl, muddat ---

@router.message(DocFlow.search, F.text, ~F.text.startswith("/"), F.chat.type == ChatType.PRIVATE)
async def on_owner_search(message: Message, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    data = await state.get_data()
    owner, intent = data.get("doc_owner"), data.get("doc_intent", "add")
    pattern = f"%{message.text.strip()[:64]}%"
    if owner == DocumentOwner.DRIVER.value:
        stmt = (await scoped_drivers(user)).where(or_(Driver.full_name.ilike(pattern), Driver.phone.ilike(pattern)))
        items = [(d.id, d.full_name or d.samsara_id) for d in (await session.scalars(stmt.limit(MAX_SEARCH))).all()]
    else:
        stmt = (await scoped_vehicles(user)).where(or_(Vehicle.name.ilike(pattern), Vehicle.plate.ilike(pattern)))
        items = [(v.id, v.name or v.samsara_id) for v in (await session.scalars(stmt.limit(MAX_SEARCH))).all()]
    if not items:
        await message.answer(t("browse.nothing", lang) + "\n" + t("docs.ask_owner_search", lang))
        return
    await state.set_state(None)
    action = "list" if intent == "list" else "add"
    rows = [[_btn(f"{'👤' if owner == DocumentOwner.DRIVER.value else '🚚'} {label}", action, owner=owner, owner_id=item_id)]
            for item_id, label in items]
    await message.answer(t("docs.pick_owner", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(DocFlow.file, F.document | F.photo, F.chat.type == ChatType.PRIVATE)
async def on_file(message: Message, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    if message.document:
        file = message.document
        meta = {"file_id": file.file_id, "file_unique_id": file.file_unique_id, "file_name": file.file_name,
                "mime_type": file.mime_type, "file_size": file.file_size}
    else:
        photo = message.photo[-1]  # eng katta o'lcham
        meta = {"file_id": photo.file_id, "file_unique_id": photo.file_unique_id, "file_name": None,
                "mime_type": "image/jpeg", "file_size": photo.file_size}
    if not svc.is_allowed_file(meta["file_name"], meta["mime_type"]):
        await message.answer(t("docs.bad_type", lang))
        return
    await state.update_data(doc_file=meta, doc_caption=(message.caption or "").strip()[:200])
    data = await state.get_data()
    if data.get("doc_type") in {d.value for d in svc.EXPIRING_TYPES}:
        await state.set_state(DocFlow.expiry)
        markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("docs.skip_expiry", lang), "noexpiry")]])
        await message.answer(t("docs.ask_expiry", lang), reply_markup=markup)
        return
    await _save(message, session, user, lang, state, None)


@router.message(DocFlow.file, F.chat.type == ChatType.PRIVATE)
async def on_file_invalid(message: Message, lang: str) -> None:
    await message.answer(t("docs.send_file_hint", lang))


@router.message(DocFlow.expiry, F.text, ~F.text.startswith("/"), F.chat.type == ChatType.PRIVATE)
async def on_expiry(message: Message, session: AsyncSession, user: User, lang: str, state: FSMContext) -> None:
    expires = svc.parse_date(message.text)
    if expires is None:
        await message.answer(t("docs.bad_date", lang))
        return
    await _save(message, session, user, lang, state, expires)


async def _save(target: Message, session: AsyncSession, user: User, lang: str, state: FSMContext, expires) -> None:
    data = await state.get_data()
    await state.clear()
    meta = data.get("doc_file")
    if not meta or not data.get("doc_type"):
        await target.answer(t("broadcast.expired", lang))
        return
    doc_type = data["doc_type"]
    title = data.get("doc_caption") or meta["file_name"] or f"{t(f'docs.type.{doc_type}', lang)} {datetime.now(UTC):%Y-%m-%d}"
    try:
        document = await svc.add_document(
            session, user,
            owner_type=data.get("doc_owner") or DocumentOwner.COMPANY.value,
            owner_id=data.get("doc_owner_id"),
            doc_type=doc_type,
            title=title,
            expires_at=expires,
            **meta,
        )
    except DocumentError as error:
        await target.answer(error.render(lang))
        return
    await target.answer(t("docs.saved", lang, title=escape(document.title)),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn(t("docs.open", lang), "get", doc_id=document.id)]]))
