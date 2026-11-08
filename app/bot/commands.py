"""Telegram "/" menyusi: har foydalanuvchiga o'z shaxsiy chatida faqat ruxsati bor buyruqlar ko'rinadi."""

import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeChat, BotCommandScopeDefault

from app.core.i18n import LANGUAGES, t
from app.core.permissions import Permission
from app.db.models import User, UserStatus
from app.services.users import user_permissions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandSpec:
    name: str
    # Hech biri bo'lmasa — har qanday faol foydalanuvchi; bir nechta bo'lsa — kamida bittasi kerak.
    any_of: tuple[Permission, ...] = ()


USER_COMMANDS: list[CommandSpec] = [
    CommandSpec("menu"),
    CommandSpec("alerts", (Permission.VIEW_ALERTS,)),
    CommandSpec("vehicles", (Permission.VIEW_VEHICLES,)),
    CommandSpec("where", (Permission.VIEW_VEHICLES,)),
    CommandSpec("drivers", (Permission.VIEW_VEHICLES,)),
    CommandSpec("hos", (Permission.VIEW_VEHICLES,)),
    CommandSpec("fuel", (Permission.VIEW_VEHICLES,)),
    CommandSpec("docs"),
    CommandSpec("chat"),
    CommandSpec("reports", (Permission.VIEW_REPORTS,)),
    CommandSpec("stats", (Permission.VIEW_REPORTS,)),
    CommandSpec("settings"),
    CommandSpec("profile"),
    CommandSpec("mute"),
    CommandSpec("unmute"),
    CommandSpec(
        "admin",
        (
            Permission.MANAGE_USERS,
            Permission.MANAGE_GROUPS,
            Permission.MANAGE_SETTINGS,
            Permission.SEND_BROADCAST,
            Permission.VIEW_AUDIT_LOG,
        ),
    ),
    CommandSpec("users", (Permission.MANAGE_USERS,)),
    CommandSpec("groups", (Permission.MANAGE_GROUPS,)),
    CommandSpec("depots", (Permission.MANAGE_SETTINGS,)),
    CommandSpec("broadcast", (Permission.SEND_BROADCAST,)),
    CommandSpec("status", (Permission.MANAGE_SETTINGS,)),
    CommandSpec("help"),
    CommandSpec("cancel"),
]

GUEST_COMMANDS = ["start", "help", "cancel"]
GROUP_ADMIN_COMMANDS = ["bind", "unbind"]


async def available_commands(user: User | None) -> list[str]:
    if user is None or user.status != UserStatus.ACTIVE:
        return list(GUEST_COMMANDS)
    permissions = await user_permissions(user)
    return [c.name for c in USER_COMMANDS if not c.any_of or any(p in permissions for p in c.any_of)]


def _bot_commands(names: list[str], lang: str | None) -> list[BotCommand]:
    return [BotCommand(command=name, description=t(f"cmd.{name}", lang)) for name in names]


async def sync_user_commands(bot: Bot, user: User) -> None:
    """Rol, holat yoki til o'zgarganda chaqiriladi."""
    scope = BotCommandScopeChat(chat_id=user.telegram_id)
    try:
        if user.status == UserStatus.ACTIVE:
            await bot.set_my_commands(_bot_commands(await available_commands(user), user.language), scope=scope)
        else:
            await bot.delete_my_commands(scope=scope)
    except TelegramAPIError as error:
        logger.warning("Foydalanuvchi %s buyruqlari yangilanmadi: %s", user.telegram_id, error)


async def clear_user_commands(bot: Bot, telegram_id: int) -> None:
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=telegram_id))
    except TelegramAPIError as error:
        logger.warning("Foydalanuvchi %s buyruqlari o'chirilmadi: %s", telegram_id, error)


async def setup_default_commands(bot: Bot) -> None:
    for lang in LANGUAGES:
        await bot.set_my_commands(_bot_commands(GUEST_COMMANDS, lang), scope=BotCommandScopeDefault(), language_code=lang)
        await bot.set_my_commands(
            _bot_commands(GROUP_ADMIN_COMMANDS, lang), scope=BotCommandScopeAllChatAdministrators(), language_code=lang
        )
    await bot.set_my_commands(_bot_commands(GUEST_COMMANDS, None), scope=BotCommandScopeDefault())
    await bot.set_my_commands(_bot_commands(GROUP_ADMIN_COMMANDS, None), scope=BotCommandScopeAllChatAdministrators())
