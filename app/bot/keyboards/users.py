from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.bot.texts import PERMISSION_LABELS, POSITION_SUGGESTIONS, ROLE_LABELS, STATUS_LABELS
from app.core.permissions import Permission
from app.db.models import Depot, User, UserRole, UserStatus


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


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamimni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def position_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=p) for p in POSITION_SUGGESTIONS[i:i + 2]] for i in range(0, len(POSITION_SUGGESTIONS), 2)]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


class RegistrationDepot(CallbackData, prefix="rd"):
    depot_id: int  # 0 — "bilmayman"


def registration_depot_keyboard(depots: list[Depot]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"🏢 {d.name}", callback_data=RegistrationDepot(depot_id=d.id).pack())] for d in depots]
    rows.append([InlineKeyboardButton(text="🤷 Bilmayman", callback_data=RegistrationDepot(depot_id=0).pack())])
    return _markup(rows)


# --- Tasdiqlash (admin tomoni) ---

def request_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return _markup([[_btn("✅ Tasdiqlash", "approve", user_id), _btn("❌ Rad etish", "reject", user_id)]])


def role_keyboard(user_id: int, roles: list[UserRole], set_action: str, back_action: str) -> InlineKeyboardMarkup:
    rows = [[_btn(ROLE_LABELS[r], set_action, user_id, r.value)] for r in roles]
    rows.append([_btn("⬅️ Orqaga", back_action, user_id)])
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

def user_list_keyboard(users: list[User], page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    for user in users:
        status_icon = STATUS_LABELS[user.status].split()[0]
        role_icon = ROLE_LABELS[user.role].split()[0]
        label = f"{status_icon} {role_icon} {user.full_name or user.username or user.telegram_id}"
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


def user_card_keyboard(target: User, actor: User, page: str = "0") -> InlineKeyboardMarkup:
    uid = target.id
    rows: list[list[InlineKeyboardButton]] = []
    if target.status == UserStatus.PENDING and target.phone:
        rows.append([_btn("✅ Tasdiqlash", "approve", uid), _btn("❌ Rad etish", "reject", uid)])
    elif target.status == UserStatus.ACTIVE:
        rows.append([_btn("🎭 Rol", "role", uid), _btn("🏢 Depolar", "depots", uid)])
        if actor.role == UserRole.OWNER:
            rows.append([_btn("🔑 Individual huquqlar", "perms", uid)])
    if target.status == UserStatus.BLOCKED:
        rows.append([_btn("✅ Blokdan chiqarish", "unblock", uid)])
    else:
        rows.append([_btn("🚫 Bloklash", "block", uid)])
    rows.append([_btn("🗑 O'chirish", "delete", uid)])
    rows.append([_btn("⬅️ Ro'yxatga", "list", arg=page)])
    return _markup(rows)


def confirm_delete_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return _markup([[_btn("🗑 Ha, o'chirilsin", "delok", user_id), _btn("⬅️ Yo'q", "view", user_id)]])


def permissions_keyboard(user_id: int, effective: set[Permission], overrides: dict[str, bool]) -> InlineKeyboardMarkup:
    rows = []
    for permission in Permission:
        icon = "✅" if permission in effective else "▫️"
        marker = {True: " · +", False: " · −"}.get(overrides.get(permission.value), "")
        rows.append([_btn(f"{icon} {PERMISSION_LABELS[permission]}{marker}", "perm", user_id, permission.value)])
    rows.append([_btn("⬅️ Orqaga", "view", user_id)])
    return _markup(rows)
