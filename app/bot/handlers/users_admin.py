from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.keyboards.users import (
    UserAdmin,
    confirm_delete_keyboard,
    depot_keyboard,
    permissions_keyboard,
    request_keyboard,
    role_keyboard,
    user_card_keyboard,
    user_list_keyboard,
)
from app.bot.notify import send_safe
from app.core.permissions import Permission
from app.db.models import User, UserRole, UserStatus
from app.services import user_admin
from app.services.depots import list_depots
from app.services.user_admin import GLOBAL_ROLES, AdminError, assignable_roles
from app.services.users import display_name, user_permissions

router = Router(name="users_admin")

MANAGE = {"permission": Permission.MANAGE_USERS}


async def _list_view(session: AsyncSession, page: int) -> tuple[str, InlineKeyboardMarkup]:
    users, pages = await user_admin.list_users(session, page)
    counts = dict((await session.execute(select(User.status, func.count()).group_by(User.status))).all())
    header = (
        f"👥 <b>Foydalanuvchilar</b>\n"
        f"⏳ Kutilmoqda: {counts.get(UserStatus.PENDING, 0)} · ✅ Faol: {counts.get(UserStatus.ACTIVE, 0)} · "
        f"🚫 Bloklangan: {counts.get(UserStatus.BLOCKED, 0)}"
    )
    return header, user_list_keyboard(users, min(page, pages - 1), pages)


@router.message(Command("users"), flags=MANAGE)
async def cmd_users(message: Message, session: AsyncSession) -> None:
    text, markup = await _list_view(session, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(UserAdmin.filter(F.action == "list"), flags=MANAGE)
async def cb_list(callback: CallbackQuery, callback_data: UserAdmin, session: AsyncSession) -> None:
    text, markup = await _list_view(session, int(callback_data.arg or 0))
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(UserAdmin.filter(), flags=MANAGE)
async def cb_user_action(callback: CallbackQuery, callback_data: UserAdmin, session: AsyncSession, user: User) -> None:
    target = await session.get(User, callback_data.user_id)
    if target is None:
        await callback.answer("Foydalanuvchi topilmadi — o'chirilgan bo'lishi mumkin.", show_alert=True)
        return
    handler = _ACTIONS.get(callback_data.action)
    if handler is None:
        await callback.answer()
        return
    try:
        await handler(callback, callback_data, session, user, target)
    except AdminError as error:
        await callback.answer(str(error), show_alert=True)


async def _card(callback: CallbackQuery, target: User, actor: User, note: str | None = None, page: str = "0") -> None:
    text = texts.user_summary(target) + (f"\n\n{note}" if note else "")
    await callback.message.edit_text(text, reply_markup=user_card_keyboard(target, actor, page))


# --- Ko'rish ---

async def _view(callback, data: UserAdmin, session, actor, target) -> None:
    await _card(callback, target, actor, page=data.arg or "0")
    await callback.answer()


# --- Tasdiqlash oqimi (so'rov xabaridan yoki kartadan) ---

async def _approve_start(callback, data, session, actor, target) -> None:
    if target.status != UserStatus.PENDING:
        await callback.message.edit_text(texts.user_summary(target) + "\n\nℹ️ Allaqachon ko'rib chiqilgan.")
        await callback.answer("Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True)
        return
    await callback.message.edit_text(
        texts.registration_request(target) + "\n\n<b>Rolni tanlang:</b>",
        reply_markup=role_keyboard(target.id, assignable_roles(actor), "apsetrole", "request"),
    )
    await callback.answer()


async def _back_to_request(callback, data, session, actor, target) -> None:
    await callback.message.edit_text(texts.registration_request(target), reply_markup=request_keyboard(target.id))
    await callback.answer()


async def _approve_set_role(callback, data, session, actor, target) -> None:
    if target.status != UserStatus.PENDING:
        raise AdminError("Bu so'rov allaqachon ko'rib chiqilgan.")
    role = UserRole(data.arg)
    await user_admin.set_role(session, actor, target, role)
    depots = await list_depots(session)
    if role in GLOBAL_ROLES or not depots:
        await _approve_finish(callback, data, session, actor, target)
        return
    await callback.message.edit_text(
        texts.registration_request(target) + "\n\n<b>Depolarni belgilang</b> (bir nechtasini tanlash mumkin):",
        reply_markup=depot_keyboard(
            target.id, depots, {d.id for d in target.depots}, "apdep", "apdone", "✅ Tasdiqlash"
        ),
    )
    await callback.answer()


async def _approve_toggle_depot(callback, data, session, actor, target) -> None:
    await user_admin.toggle_depot(session, actor, target, int(data.arg))
    depots = await list_depots(session)
    await callback.message.edit_reply_markup(
        reply_markup=depot_keyboard(target.id, depots, {d.id for d in target.depots}, "apdep", "apdone", "✅ Tasdiqlash")
    )
    await callback.answer()


async def _approve_finish(callback, data, session, actor, target) -> None:
    await user_admin.approve(session, actor, target)
    await callback.message.edit_text(
        texts.user_summary(target) + f"\n\n✅ Tasdiqlandi — {escape(display_name(actor))}"
    )
    await callback.answer("✅ Tasdiqlandi")
    if target.role in GLOBAL_ROLES:
        depots = "barchasi"
    else:
        depots = ", ".join(escape(d.name) for d in target.depots) or "—"
    await send_safe(
        callback.bot,
        target.telegram_id,
        f"✅ So'rovingiz tasdiqlandi!\nRolingiz: {texts.ROLE_LABELS[target.role]}\nDepolar: {depots}",
    )


async def _reject(callback, data, session, actor, target) -> None:
    await user_admin.reject(session, actor, target)
    await callback.message.edit_text(texts.user_summary(target) + f"\n\n❌ Rad etildi — {escape(display_name(actor))}")
    await callback.answer("❌ Rad etildi")
    await send_safe(callback.bot, target.telegram_id, "❌ Ro'yxatdan o'tish so'rovingiz rad etildi.")


# --- Rol va depolar (faol foydalanuvchi) ---

async def _role_menu(callback, data, session, actor, target) -> None:
    await callback.message.edit_text(
        texts.user_summary(target) + "\n\n<b>Yangi rolni tanlang:</b>",
        reply_markup=role_keyboard(target.id, assignable_roles(actor), "setrole", "view"),
    )
    await callback.answer()


async def _set_role(callback, data, session, actor, target) -> None:
    role = UserRole(data.arg)
    await user_admin.set_role(session, actor, target, role)
    await _card(callback, target, actor, note=f"🎭 Rol o'zgartirildi: {texts.ROLE_LABELS[role]}")
    await callback.answer("Saqlandi")
    await send_safe(callback.bot, target.telegram_id, f"🎭 Rolingiz o'zgardi: {texts.ROLE_LABELS[role]}")


async def _depots_menu(callback, data, session, actor, target) -> None:
    if target.role in GLOBAL_ROLES:
        raise AdminError("Owner/Admin barcha depolarni ko'radi — depo biriktirish shart emas.")
    depots = await list_depots(session)
    if not depots:
        raise AdminError("Depolar hali yo'q. /depots orqali qo'shing.")
    await callback.message.edit_text(
        texts.user_summary(target) + "\n\n<b>Depolarni belgilang:</b>",
        reply_markup=depot_keyboard(target.id, depots, {d.id for d in target.depots}, "dep", "view", "✅ Tayyor"),
    )
    await callback.answer()


async def _toggle_depot(callback, data, session, actor, target) -> None:
    await user_admin.toggle_depot(session, actor, target, int(data.arg))
    depots = await list_depots(session)
    await callback.message.edit_text(
        texts.user_summary(target) + "\n\n<b>Depolarni belgilang:</b>",
        reply_markup=depot_keyboard(target.id, depots, {d.id for d in target.depots}, "dep", "view", "✅ Tayyor"),
    )
    await callback.answer()


# --- Bloklash va o'chirish ---

async def _block(callback, data, session, actor, target) -> None:
    await user_admin.set_blocked(session, actor, target, True)
    await _card(callback, target, actor, note="🚫 Bloklandi")
    await callback.answer("Bloklandi")
    await send_safe(callback.bot, target.telegram_id, texts.BLOCKED)


async def _unblock(callback, data, session, actor, target) -> None:
    await user_admin.set_blocked(session, actor, target, False)
    await _card(callback, target, actor, note="✅ Blokdan chiqarildi")
    await callback.answer("Blokdan chiqarildi")
    if target.status == UserStatus.ACTIVE:
        await send_safe(callback.bot, target.telegram_id, "✅ Kirish huquqingiz tiklandi.")


async def _delete_confirm(callback, data, session, actor, target) -> None:
    await callback.message.edit_text(
        texts.user_summary(target) + "\n\n⚠️ <b>Foydalanuvchi butunlay o'chirilsinmi?</b>",
        reply_markup=confirm_delete_keyboard(target.id),
    )
    await callback.answer()


async def _delete(callback, data, session, actor, target) -> None:
    name = escape(display_name(target))
    await user_admin.delete_user(session, actor, target)
    text, markup = await _list_view(session, 0)
    await callback.message.edit_text(f"🗑 {name} o'chirildi.\n\n{text}", reply_markup=markup)
    await callback.answer("O'chirildi")


# --- Individual huquqlar (faqat Owner) ---

async def _permissions_view(callback, target: User, note: str | None = None) -> None:
    effective = await user_permissions(target)
    overrides = {o.permission: o.allowed for o in target.permission_overrides}
    text = (
        texts.user_summary(target)
        + "\n\n<b>🔑 Individual huquqlar</b>\nBosish: rol standarti → <b>+</b> berish → <b>−</b> olib qo'yish → standart"
        + (f"\n\n{note}" if note else "")
    )
    await callback.message.edit_text(text, reply_markup=permissions_keyboard(target.id, effective, overrides))


async def _permissions(callback, data, session, actor, target) -> None:
    if actor.role != UserRole.OWNER:
        raise AdminError("Individual huquqlarni faqat Owner o'zgartiradi.")
    if target.status != UserStatus.ACTIVE:
        raise AdminError("Faqat faol foydalanuvchi uchun.")
    await _permissions_view(callback, target)
    await callback.answer()


async def _cycle_permission(callback, data, session, actor, target) -> None:
    await user_admin.cycle_override(session, actor, target, Permission(data.arg))
    await _permissions_view(callback, target)
    await callback.answer("Saqlandi")


_ACTIONS = {
    "view": _view,
    "approve": _approve_start,
    "request": _back_to_request,
    "apsetrole": _approve_set_role,
    "apdep": _approve_toggle_depot,
    "apdone": _approve_finish,
    "reject": _reject,
    "role": _role_menu,
    "setrole": _set_role,
    "depots": _depots_menu,
    "dep": _toggle_depot,
    "block": _block,
    "unblock": _unblock,
    "delete": _delete_confirm,
    "delok": _delete,
    "perms": _permissions,
    "perm": _cycle_permission,
}
