from dataclasses import dataclass

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import User
from app.services.users import user_permissions

ADMIN_PERMISSIONS = (
    Permission.MANAGE_USERS,
    Permission.MANAGE_GROUPS,
    Permission.MANAGE_SETTINGS,
    Permission.SEND_BROADCAST,
    Permission.VIEW_AUDIT_LOG,
)


@dataclass(frozen=True)
class Section:
    key: str
    any_of: tuple[Permission, ...] = ()


SECTIONS = [
    Section("alerts", (Permission.VIEW_ALERTS,)),
    Section("vehicles", (Permission.VIEW_VEHICLES,)),
    Section("drivers", (Permission.VIEW_VEHICLES,)),
    Section("hos", (Permission.VIEW_VEHICLES,)),
    Section("fuel", (Permission.VIEW_VEHICLES,)),
    Section("docs"),  # haydovchi ham (o'z BOL/POD'lari) — kirish handler'da tekshiriladi
    Section("reports", (Permission.VIEW_REPORTS,)),
    Section("settings"),
    Section("profile"),
    Section("admin", ADMIN_PERMISSIONS),
]
REPLY_SECTIONS = ("alerts", "vehicles", "docs", "reports", "settings", "menu")


class MenuAction(CallbackData, prefix="mn"):
    section: str


async def allowed_sections(user: User) -> list[str]:
    permissions = await user_permissions(user)
    return [s.key for s in SECTIONS if not s.any_of or any(p in permissions for p in s.any_of)]


def section_label(key: str, lang: str | None) -> str:
    return t(f"menu.{key}", lang)


async def main_reply_keyboard(user: User) -> ReplyKeyboardMarkup:
    allowed = set(await allowed_sections(user)) | {"menu"}
    labels = [section_label(key, user.language) for key in REPLY_SECTIONS if key in allowed]
    rows = [[KeyboardButton(text=label) for label in labels[i:i + 2]] for i in range(0, len(labels), 2)]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


async def main_inline_menu(user: User) -> InlineKeyboardMarkup:
    keys = await allowed_sections(user)
    buttons = [
        InlineKeyboardButton(text=section_label(key, user.language), callback_data=MenuAction(section=key).pack())
        for key in keys
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 2] for i in range(0, len(buttons), 2)])


def home_button(lang: str | None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("common.home", lang), callback_data=MenuAction(section="home").pack())
