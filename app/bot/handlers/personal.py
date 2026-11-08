"""Shaxsiy bo'limlar: /help, /profile, /settings, /mute, /unmute."""

from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.commands import available_commands, sync_user_commands
from app.bot.keyboards.menu import home_button, main_reply_keyboard
from app.core.i18n import LANGUAGE_NAMES, LANGUAGES, t
from app.db.models import User
from app.services import preferences
from app.services.alerts.formatter import format_time
from app.services.preferences import MUTE_HOURS, NOTIFY_LEVELS, QUIET_PRESETS, TIMEZONES, PreferenceError
from app.services.users import user_permissions

router = Router(name="personal")


class SettingsAction(CallbackData, prefix="st"):
    field: str
    value: str | None = None


class MuteAction(CallbackData, prefix="mu"):
    hours: int


def _btn(text: str, field: str, value: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=SettingsAction(field=field, value=value).pack())


# --- /help ---

async def show_help(target: Message, user: User | None, lang: str) -> None:
    lines = [t("help.title", lang), ""]
    lines += [f"/{name} — {t(f'cmd.{name}', lang)}" for name in await available_commands(user)]
    lines += ["", t("help.footer", lang)]
    await target.answer("\n".join(lines))


@router.message(Command("help"), flags={"public": True})
async def cmd_help(message: Message, user: User | None, lang: str) -> None:
    await show_help(message, user, lang)


# --- /profile ---

def _mute_line(user: User, lang: str) -> str:
    if preferences.is_muted(user, datetime.now(UTC)):
        return t("profile.muted_until", lang, time=format_time(user.muted_until, user.timezone))
    return t("profile.not_muted", lang)


async def show_profile(target: Message, user: User, lang: str) -> None:
    permissions = sorted(await user_permissions(user), key=lambda p: p.value)
    perms = "\n".join(f"  • {texts.permission_label(p, lang)}" for p in permissions) or "  —"
    text = (
        f"{t('profile.title', lang)}\n\n{texts.user_summary(user, lang)}\n"
        f"🌐 {LANGUAGE_NAMES[user.language] if user.language in LANGUAGE_NAMES else user.language}\n"
        f"🕒 {escape(user.timezone)}\n"
        f"🔔 {t(f'settings.notify.{user.notify_level}', lang)}\n"
        f"{_mute_line(user, lang)}\n\n"
        f"{t('profile.permissions', lang)}\n{perms}"
    )
    await target.answer(text)


@router.message(Command("profile"))
async def cmd_profile(message: Message, user: User, lang: str) -> None:
    await show_profile(message, user, lang)


# --- /settings ---

def _settings_text(user: User, lang: str) -> str:
    quiet = preferences.quiet_preset_key(user)
    return t(
        "settings.text",
        lang,
        language=LANGUAGE_NAMES.get(user.language, user.language),
        timezone=escape(user.timezone),
        notify=t(f"settings.notify.{user.notify_level}", lang),
        quiet=t("settings.quiet_off", lang) if quiet == "off" else f"{user.quiet_start:02d}:00–{user.quiet_end:02d}:00",
        mute=_mute_line(user, lang),
    )


def _settings_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(t("settings.btn_language", lang), "lang"), _btn(t("settings.btn_timezone", lang), "tz")],
            [_btn(t("settings.btn_notify", lang), "notify"), _btn(t("settings.btn_quiet", lang), "quiet")],
            [home_button(lang)],
        ]
    )


def _options_markup(field: str, user: User, lang: str) -> InlineKeyboardMarkup:
    if field == "lang":
        options = [(code, LANGUAGE_NAMES[code]) for code in LANGUAGES]
        current = user.language
    elif field == "tz":
        options = [(tz, tz) for tz in TIMEZONES]
        current = user.timezone
    elif field == "notify":
        options = [(level, t(f"settings.notify.{level}", lang)) for level in NOTIFY_LEVELS]
        current = user.notify_level
    else:
        options = [
            (key, t("settings.quiet_off", lang) if key == "off" else f"{a:02d}:00–{b:02d}:00")
            for key, value in QUIET_PRESETS.items()
            for a, b in [value or (0, 0)]
        ]
        current = preferences.quiet_preset_key(user)
    rows = [[_btn(f"{'✅ ' if value == current else ''}{label}", f"set_{field}", value)] for value, label in options]
    rows.append([_btn(t("common.back", lang), "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_settings(target: Message, user: User, lang: str) -> None:
    await target.answer(_settings_text(user, lang), reply_markup=_settings_markup(lang))


@router.message(Command("settings"))
async def cmd_settings(message: Message, user: User, lang: str) -> None:
    await show_settings(message, user, lang)


@router.callback_query(SettingsAction.filter(F.field == "menu"))
async def cb_settings_menu(callback: CallbackQuery, user: User, lang: str) -> None:
    await callback.message.edit_text(_settings_text(user, lang), reply_markup=_settings_markup(lang))
    await callback.answer()


@router.callback_query(SettingsAction.filter(F.field.in_({"lang", "tz", "notify", "quiet"})))
async def cb_settings_options(callback: CallbackQuery, callback_data: SettingsAction, user: User, lang: str) -> None:
    hint = t(f"settings.hint.{callback_data.field}", lang)
    await callback.message.edit_text(
        f"{_settings_text(user, lang)}\n\n{hint}", reply_markup=_options_markup(callback_data.field, user, lang)
    )
    await callback.answer()


@router.callback_query(SettingsAction.filter(F.field.startswith("set_")))
async def cb_settings_set(
    callback: CallbackQuery, callback_data: SettingsAction, session: AsyncSession, user: User, lang: str
) -> None:
    field = callback_data.field.removeprefix("set_")
    try:
        await preferences.set_preference(session, user, field, callback_data.value or "")
    except PreferenceError as error:
        await callback.answer(error.render(lang), show_alert=True)
        return
    lang = user.language
    await callback.message.edit_text(_settings_text(user, lang), reply_markup=_settings_markup(lang))
    await callback.answer(t("common.saved", lang))
    if field == "lang":
        # Buyruqlar menyusi va pastdagi klaviatura ham yangi tilda bo'lishi uchun.
        await sync_user_commands(callback.bot, user)
        await callback.message.answer(t("settings.language_changed", lang), reply_markup=await main_reply_keyboard(user))


# --- /mute, /unmute ---

def _mute_markup(lang: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=t("mute.hours", lang, hours=h), callback_data=MuteAction(hours=h).pack())
        for h in MUTE_HOURS
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


@router.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject, session: AsyncSession, user: User, lang: str) -> None:
    if not command.args:
        await message.answer(t("mute.choose", lang), reply_markup=_mute_markup(lang))
        return
    try:
        until = await preferences.mute(session, user, int(command.args.strip()))
    except ValueError:
        await message.answer(t("mute.invalid_hours", lang, max=preferences.MAX_MUTE_HOURS))
        return
    except PreferenceError as error:
        await message.answer(error.render(lang))
        return
    await message.answer(t("mute.done", lang, time=format_time(until, user.timezone)))


@router.callback_query(MuteAction.filter())
async def cb_mute(callback: CallbackQuery, callback_data: MuteAction, session: AsyncSession, user: User, lang: str) -> None:
    until = await preferences.mute(session, user, callback_data.hours)
    await callback.message.edit_text(t("mute.done", lang, time=format_time(until, user.timezone)))
    await callback.answer()


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await preferences.unmute(session, user)
    await message.answer(t("mute.undone", lang))
