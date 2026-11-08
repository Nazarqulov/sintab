from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.commands import clear_user_commands, sync_user_commands
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
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import User, UserRole, UserStatus
from app.services import user_admin
from app.services.depots import list_depots
from app.services.user_admin import GLOBAL_ROLES, AdminError, assignable_roles
from app.services.users import display_name, user_permissions

router = Router(name="users_admin")

MANAGE = {"permission": Permission.MANAGE_USERS}


async def list_view(session: AsyncSession, page: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    users, pages = await user_admin.list_users(session, page)
    counts = dict((await session.execute(select(User.status, func.count()).group_by(User.status))).all())
    header = t(
        "users.header",
        lang,
        pending=counts.get(UserStatus.PENDING, 0),
        active=counts.get(UserStatus.ACTIVE, 0),
        blocked=counts.get(UserStatus.BLOCKED, 0),
    )
    return header, user_list_keyboard(users, min(page, pages - 1), pages, lang)


@router.message(Command("users"), flags=MANAGE)
async def cmd_users(message: Message, session: AsyncSession, lang: str) -> None:
    text, markup = await list_view(session, 0, lang)
    await message.answer(text, reply_markup=markup)


@router.callback_query(UserAdmin.filter(F.action == "list"), flags=MANAGE)
async def cb_list(callback: CallbackQuery, callback_data: UserAdmin, session: AsyncSession, lang: str) -> None:
    text, markup = await list_view(session, int(callback_data.arg or 0), lang)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(UserAdmin.filter(), flags=MANAGE)
async def cb_user_action(
    callback: CallbackQuery, callback_data: UserAdmin, session: AsyncSession, user: User, lang: str
) -> None:
    target = await session.get(User, callback_data.user_id)
    if target is None:
        await callback.answer(t("users.not_found", lang), show_alert=True)
        return
    handler = _ACTIONS.get(callback_data.action)
    if handler is None:
        await callback.answer()
        return
    try:
        await handler(_Ctx(callback, callback_data, session, user, target, lang))
    except AdminError as error:
        await callback.answer(error.render(lang), show_alert=True)


class _Ctx:
    def __init__(self, callback, data, session, actor, target, lang) -> None:
        self.callback: CallbackQuery = callback
        self.data: UserAdmin = data
        self.session: AsyncSession = session
        self.actor: User = actor
        self.target: User = target
        self.lang: str = lang

    def summary(self, extra_key: str | None = None, **kwargs) -> str:
        text = texts.user_summary(self.target, self.lang)
        return f"{text}\n\n{t(extra_key, self.lang, **kwargs)}" if extra_key else text

    async def card(self, note_key: str | None = None, **kwargs) -> None:
        await self.callback.message.edit_text(
            self.summary(note_key, **kwargs),
            reply_markup=user_card_keyboard(self.target, self.actor, self.lang, self.data.arg or "0"),
        )

    async def notify_target(self, key: str, **kwargs) -> None:
        await send_safe(self.callback.bot, self.target.telegram_id, t(key, self.target.language, **kwargs))


# --- Ko'rish ---

async def _view(c: _Ctx) -> None:
    await c.card()
    await c.callback.answer()


# --- Tasdiqlash oqimi (so'rov xabaridan yoki kartadan) ---

async def _approve_start(c: _Ctx) -> None:
    if c.target.status != UserStatus.PENDING:
        await c.callback.message.edit_text(c.summary("users.already_reviewed_note"))
        await c.callback.answer(t("admin_err.already_reviewed", c.lang), show_alert=True)
        return
    await c.callback.message.edit_text(
        f"{texts.registration_request(c.target, c.lang)}\n\n{t('users.choose_role', c.lang)}",
        reply_markup=role_keyboard(c.target.id, assignable_roles(c.actor), "apsetrole", "request", c.lang),
    )
    await c.callback.answer()


async def _back_to_request(c: _Ctx) -> None:
    await c.callback.message.edit_text(
        texts.registration_request(c.target, c.lang), reply_markup=request_keyboard(c.target.id, c.lang)
    )
    await c.callback.answer()


def _approval_depots_markup(c: _Ctx, depots) -> InlineKeyboardMarkup:
    return depot_keyboard(
        c.target.id, depots, {d.id for d in c.target.depots}, "apdep", "apdone", t("users.approve", c.lang)
    )


async def _approve_set_role(c: _Ctx) -> None:
    if c.target.status != UserStatus.PENDING:
        raise AdminError("admin_err.already_reviewed")
    role = UserRole(c.data.arg)
    await user_admin.set_role(c.session, c.actor, c.target, role)
    depots = await list_depots(c.session)
    if role in GLOBAL_ROLES or not depots:
        await _approve_finish(c)
        return
    await c.callback.message.edit_text(
        f"{texts.registration_request(c.target, c.lang)}\n\n{t('users.choose_depots', c.lang)}",
        reply_markup=_approval_depots_markup(c, depots),
    )
    await c.callback.answer()


async def _approve_toggle_depot(c: _Ctx) -> None:
    await user_admin.toggle_depot(c.session, c.actor, c.target, int(c.data.arg))
    await c.callback.message.edit_reply_markup(reply_markup=_approval_depots_markup(c, await list_depots(c.session)))
    await c.callback.answer()


async def _approve_finish(c: _Ctx) -> None:
    await user_admin.approve(c.session, c.actor, c.target)
    await c.callback.message.edit_text(c.summary("users.approved_by", actor=escape(display_name(c.actor))))
    await c.callback.answer(t("users.approved_toast", c.lang))
    target_lang = c.target.language
    if c.target.role in GLOBAL_ROLES:
        depots = t("common.all", target_lang)
    else:
        depots = ", ".join(escape(d.name) for d in c.target.depots) or "—"
    await c.notify_target("reg.approved", role=texts.role_label(c.target.role, target_lang), depots=depots)
    await sync_user_commands(c.callback.bot, c.target)


async def _reject(c: _Ctx) -> None:
    await user_admin.reject(c.session, c.actor, c.target)
    await c.callback.message.edit_text(c.summary("users.rejected_by", actor=escape(display_name(c.actor))))
    await c.callback.answer(t("users.rejected_toast", c.lang))
    await c.notify_target("reg.rejected")


# --- Rol va depolar (faol foydalanuvchi) ---

async def _role_menu(c: _Ctx) -> None:
    await c.callback.message.edit_text(
        c.summary("users.choose_new_role"),
        reply_markup=role_keyboard(c.target.id, assignable_roles(c.actor), "setrole", "view", c.lang),
    )
    await c.callback.answer()


async def _set_role(c: _Ctx) -> None:
    role = UserRole(c.data.arg)
    await user_admin.set_role(c.session, c.actor, c.target, role)
    await c.card("users.role_changed", role=texts.role_label(role, c.lang))
    await c.callback.answer(t("common.saved", c.lang))
    await c.notify_target("users.role_changed_notice", role=texts.role_label(role, c.target.language))
    await sync_user_commands(c.callback.bot, c.target)


def _depots_markup(c: _Ctx, depots) -> InlineKeyboardMarkup:
    return depot_keyboard(c.target.id, depots, {d.id for d in c.target.depots}, "dep", "view", t("common.done", c.lang))


async def _depots_menu(c: _Ctx) -> None:
    if c.target.role in GLOBAL_ROLES:
        raise AdminError("admin_err.global_role_depots")
    depots = await list_depots(c.session)
    if not depots:
        raise AdminError("admin_err.no_depots")
    await c.callback.message.edit_text(c.summary("users.pick_depots"), reply_markup=_depots_markup(c, depots))
    await c.callback.answer()


async def _toggle_depot(c: _Ctx) -> None:
    await user_admin.toggle_depot(c.session, c.actor, c.target, int(c.data.arg))
    await c.callback.message.edit_text(
        c.summary("users.pick_depots"), reply_markup=_depots_markup(c, await list_depots(c.session))
    )
    await c.callback.answer()


# --- Bloklash va o'chirish ---

async def _block(c: _Ctx) -> None:
    await user_admin.set_blocked(c.session, c.actor, c.target, True)
    await c.card("users.blocked_note")
    await c.callback.answer(t("users.blocked_note", c.lang))
    await c.notify_target("access.blocked")
    await sync_user_commands(c.callback.bot, c.target)


async def _unblock(c: _Ctx) -> None:
    await user_admin.set_blocked(c.session, c.actor, c.target, False)
    await c.card("users.unblocked_note")
    await c.callback.answer(t("users.unblocked_note", c.lang))
    if c.target.status == UserStatus.ACTIVE:
        await c.notify_target("users.access_restored")
        await sync_user_commands(c.callback.bot, c.target)


async def _delete_confirm(c: _Ctx) -> None:
    await c.callback.message.edit_text(
        c.summary("users.confirm_delete"), reply_markup=confirm_delete_keyboard(c.target.id, c.lang)
    )
    await c.callback.answer()


async def _delete(c: _Ctx) -> None:
    name = escape(display_name(c.target))
    telegram_id = c.target.telegram_id
    await user_admin.delete_user(c.session, c.actor, c.target)
    text, markup = await list_view(c.session, 0, c.lang)
    await c.callback.message.edit_text(f"{t('users.deleted', c.lang, name=name)}\n\n{text}", reply_markup=markup)
    await c.callback.answer()
    await clear_user_commands(c.callback.bot, telegram_id)


# --- Individual huquqlar (faqat Owner) ---

async def _permissions_view(c: _Ctx) -> None:
    effective = await user_permissions(c.target)
    overrides = {o.permission: o.allowed for o in c.target.permission_overrides}
    await c.callback.message.edit_text(
        c.summary("users.perms_title"), reply_markup=permissions_keyboard(c.target.id, effective, overrides, c.lang)
    )


async def _permissions(c: _Ctx) -> None:
    if c.actor.role != UserRole.OWNER:
        raise AdminError("admin_err.overrides_owner_only")
    if c.target.status != UserStatus.ACTIVE:
        raise AdminError("admin_err.active_only")
    await _permissions_view(c)
    await c.callback.answer()


async def _cycle_permission(c: _Ctx) -> None:
    await user_admin.cycle_override(c.session, c.actor, c.target, Permission(c.data.arg))
    await _permissions_view(c)
    await c.callback.answer(t("common.saved", c.lang))
    await sync_user_commands(c.callback.bot, c.target)


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
