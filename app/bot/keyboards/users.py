from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.bot.texts import POSITION_SUGGESTIONS, permission_label, role_label
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import Depot, User, UserRole, UserStatus

STATUS_ICONS = {UserStatus.PENDING: "⏳", UserStatus.ACTIVE: "✅", UserStatus.BLOCKED: "🚫"}


class UserAdmin(CallbackData, prefix="ua"):
    action: str
    user_id: int = 0
    # aiogram bo'sh qiymatni unpack'da None qiladi — shuning uchun `str = ""` emas.
    arg: str | None = None


def _btn(text: str, action: str, user_id: int = 0, arg: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=UserAdmin(action=action, user_id=user_id, arg=arg).pack())


def _markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


# --- Ro'yxatdan o'tish (foydalanuvchi tomoni) ---

def name_keyboard(telegram_name: str | None) -> ReplyKeyboardMarkup | None:
    if not telegram_name:
        return None
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=telegram_name)]], resize_keyboard=True, one_time_keyboard=True)


def phone_keyboard(lang: str | None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("reg.phone_button", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def position_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=p) for p in POSITION_SUGGESTIONS[i:i + 2]] for i in range(0, len(POSITION_SUGGESTIONS), 2)]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


class RegistrationDepot(CallbackData, prefix="rd"):
    depot_id: int  # 0 — "bilmayman"


def registration_depot_keyboard(depots: list[Depot], lang: str | None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"🏢 {d.name}", callback_data=RegistrationDepot(depot_id=d.id).pack())] for d in depots]
    rows.append([InlineKeyboardButton(text=t("reg.depot_unknown", lang), callback_data=RegistrationDepot(depot_id=0).pack())])
    return _markup(rows)


# --- Tasdiqlash (admin tomoni) ---

def request_keyboard(user_id: int, lang: str | None) -> InlineKeyboardMarkup:
    return _markup([[_btn(t("users.approve", lang), "approve", user_id), _btn(t("users.reject", lang), "reject", user_id)]])


def role_keyboard(
    user_id: int, roles: list[UserRole], set_action: str, back_action: str, lang: str | None
) -> InlineKeyboardMarkup:
    rows = [[_btn(role_label(r, lang), set_action, user_id, r.value)] for r in roles]
    rows.append([_btn(t("common.back", lang), back_action, user_id)])
    return _markup(rows)


def depot_keyboard(
    user_id: int,
    depots: list[Depot],
    selected: set[int],
    toggle_action: str,
    done_action: str,
    done_text: str,
) -> InlineKeyboardMarkup:
    rows = [
        [_btn(f"{'☑️' if d.id in selected else '⬜️'} {d.name}", toggle_action, user_id, str(d.id))] for d in depots
    ]
    rows.append([_btn(done_text, done_action, user_id)])
    return _markup(rows)


# --- /users boshqaruvi ---

def user_list_keyboard(users: list[User], page: int, pages: int, lang: str | None) -> InlineKeyboardMarkup:
    rows = []
    for user in users:
        role_icon = role_label(user.role, lang).split()[0]
        label = f"{STATUS_ICONS[user.status]} {role_icon} {user.full_name or user.username or user.telegram_id}"
        rows.append([_btn(label[:60], "view", user.id, str(page))])
    nav = []
    if page > 0:
        nav.append(_btn("◀️", "list", arg=str(page - 1)))
    if pages > 1:
        nav.append(_btn(f"{page + 1}/{pages}", "list", arg=str(page)))
    if page < pages - 1:
        nav.append(_btn("▶️", "list", arg=str(page + 1)))
    rows.append(nav)
    return _markup(rows)


def user_card_keyboard(target: User, actor: User, lang: str | None, page: str = "0") -> InlineKeyboardMarkup:
    uid = target.id
    rows: list[list[InlineKeyboardButton]] = []
    if target.status == UserStatus.PENDING and target.phone:
        rows.append([_btn(t("users.approve", lang), "approve", uid), _btn(t("users.reject", lang), "reject", uid)])
    elif target.status == UserStatus.ACTIVE:
        rows.append([_btn(t("users.btn_role", lang), "role", uid), _btn(t("users.btn_depots", lang), "depots", uid)])
        if actor.role == UserRole.OWNER:
            rows.append([_btn(t("users.btn_perms", lang), "perms", uid)])
    if target.status == UserStatus.BLOCKED:
        rows.append([_btn(t("users.btn_unblock", lang), "unblock", uid)])
    else:
        rows.append([_btn(t("users.btn_block", lang), "block", uid)])
    rows.append([_btn(t("users.btn_delete", lang), "delete", uid)])
    rows.append([_btn(t("users.btn_to_list", lang), "list", arg=page)])
    return _markup(rows)


def confirm_delete_keyboard(user_id: int, lang: str | None) -> InlineKeyboardMarkup:
    return _markup([[_btn(t("common.yes_delete", lang), "delok", user_id), _btn(t("common.no", lang), "view", user_id)]])


def permissions_keyboard(
    user_id: int, effective: set[Permission], overrides: dict[str, bool], lang: str | None
) -> InlineKeyboardMarkup:
    rows = []
    for permission in Permission:
        icon = "✅" if permission in effective else "▫️"
        marker = {True: " · +", False: " · −"}.get(overrides.get(permission.value), "")
        rows.append([_btn(f"{icon} {permission_label(permission, lang)}{marker}", "perm", user_id, permission.value)])
    rows.append([_btn(t("common.back", lang), "view", user_id)])
    return _markup(rows)
