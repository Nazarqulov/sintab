"""/admin panel: bo'limlar, /status, audit log, tizim sozlamalari, alert darajalari, backup."""

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.broadcast import start_broadcast
from app.bot.handlers.depots import show_depots
from app.bot.handlers.groups import show_groups
from app.bot.handlers.users_admin import list_view as users_list_view
from app.bot.keyboards.menu import ADMIN_PERMISSIONS, home_button
from app.core.config import settings
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import AppSetting, AuditLog, User, UserRole
from app.services import app_settings
from app.services.alerts.formatter import format_time, type_label
from app.services.alerts.types import DEFAULT_SEVERITY, SEVERITY_OVERRIDES_SETTING, AlertType, Severity
from app.services.backup import TELEGRAM_UPLOAD_LIMIT, latest_backup
from app.services.health import collect_health
from app.services.users import display_name, user_permissions

router = Router(name="admin")

AUDIT_PAGE = 12
SETTINGS = {"permission": Permission.MANAGE_SETTINGS}


class AdminNav(CallbackData, prefix="ad"):
    section: str
    page: int = 0
    key: str | None = None
    value: str | None = None


def _btn(text: str, section: str, **kw) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AdminNav(section=section, **kw).pack())


ADMIN_SECTIONS = [
    ("users", Permission.MANAGE_USERS),
    ("groups", Permission.MANAGE_GROUPS),
    ("depots", Permission.MANAGE_SETTINGS),
    ("broadcast", Permission.SEND_BROADCAST),
    ("status", Permission.MANAGE_SETTINGS),
    ("settings", Permission.MANAGE_SETTINGS),
    ("severity", Permission.MANAGE_SETTINGS),
    ("audit", Permission.VIEW_AUDIT_LOG),
]


async def admin_panel(user: User, lang: str) -> InlineKeyboardMarkup:
    permissions = await user_permissions(user)
    buttons = [_btn(t(f"admin.section.{key}", lang), key) for key, permission in ADMIN_SECTIONS if permission in permissions]
    if user.role == UserRole.OWNER:
        buttons.append(_btn(t("admin.section.backup", lang), "backup"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)] + [[home_button(lang)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin(target: Message, user: User, lang: str) -> None:
    await target.answer(t("admin.title", lang), reply_markup=await admin_panel(user, lang))


@router.message(Command("admin"))
async def cmd_admin(message: Message, user: User, lang: str) -> None:
    permissions = await user_permissions(user)
    if not any(p in permissions for p in ADMIN_PERMISSIONS):
        await message.answer(t("access.no_permission", lang))
        return
    await show_admin(message, user, lang)


# --- /status ---

async def status_text(session: AsyncSession, user: User, lang: str) -> str:
    health = await collect_health(session)
    hours, remainder = divmod(int(health.uptime.total_seconds()), 3600)
    samsara = {
        "ok": t("health.samsara_ok", lang, ms=f"{health.samsara_ms:.0f}" if health.samsara_ms else "?"),
        "no_token": t("health.samsara_no_token", lang),
    }.get(health.samsara, t("health.samsara_error", lang, error=escape(health.samsara)))
    return t(
        "health.text",
        lang,
        db=t("health.ok", lang, ms=f"{health.db_ms:.0f}") if health.db_ok else t("health.down", lang),
        samsara=samsara,
        webhook=format_time(health.last_webhook, user.timezone) if health.last_webhook else t("health.never", lang),
        pending=health.pending,
        queue=health.queue_size if health.queue_size is not None else "—",
        deferred=health.deferred,
        failed=health.failed_24h,
        uptime=f"{hours}h {remainder // 60}m",
        mode=settings.bot_mode,
    )


@router.message(Command("status"), flags=SETTINGS)
async def cmd_status(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await message.answer(await status_text(session, user, lang))


# --- Audit log ---

async def audit_view(session: AsyncSession, user: User, lang: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = await session.scalar(select(func.count(AuditLog.id))) or 0
    pages = max((total + AUDIT_PAGE - 1) // AUDIT_PAGE, 1)
    page = min(max(page, 0), pages - 1)
    entries = (await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).offset(page * AUDIT_PAGE).limit(AUDIT_PAGE))).all()
    actors = {u.id: u for u in (await session.scalars(select(User).where(User.id.in_({e.actor_id for e in entries if e.actor_id})))).all()}
    lines = [t("admin.audit_title", lang, page=page + 1, pages=pages), ""]
    for entry in entries:
        details = ", ".join(f"{k}={v}" for k, v in (entry.details or {}).items())
        lines.append(
            f"<code>{format_time(entry.created_at, user.timezone)[:16]}</code> · {escape(display_name(actors.get(entry.actor_id)))} · "
            f"<b>{escape(entry.action)}</b> {escape(entry.target or '')} {escape(details[:80])}"
        )
    nav = []
    if page > 0:
        nav.append(_btn("◀️", "audit", page=page - 1))
    if page < pages - 1:
        nav.append(_btn("▶️", "audit", page=page + 1))
    rows = [r for r in [nav, [_btn(t("common.back", lang), "panel")]] if r]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


# --- Tizim sozlamalari ---

def _value_label(key: str, value, lang: str) -> str:
    if key == "digest_enabled":
        return t("common.yes" if value else "common.no_plain", lang)
    if key == "digest_window":
        return value
    if value in (0, -1):
        return t("admin.off", lang)
    unit = {"escalation_minutes": "min", "webhook_silence_hours": "h", "hos_warning_minutes": "min",
            "fuel_warning_percent": "%", "shift_report_hour": ":00"}.get(key, "")
    return f"{value}{unit}"


async def settings_view(session: AsyncSession, lang: str, open_key: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    current = {key: await app_settings.get_setting(session, key) for key in app_settings.DEFAULTS}
    current["digest_window"] = f"{current['digest_start_hour']}-{current['digest_end_hour']}"
    lines = [t("admin.settings_title", lang), ""]
    rows = []
    for key in app_settings.CHOICES:
        lines.append(f"{t(f'admin.setting.{key}', lang)}: <b>{_value_label(key, current[key], lang)}</b>")
        rows.append([_btn(t(f"admin.setting.{key}", lang), "settings", key=key)])
        if key == open_key:
            rows.append([
                _btn(f"{'• ' if str(choice) == str(current[key]) else ''}{_value_label(key, choice, lang)}", "setval", key=key, value=str(choice))
                for choice in app_settings.CHOICES[key]
            ])
    rows.append([_btn(t("common.back", lang), "panel")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


# --- Alert darajalari (severity override) ---

SEVERITY_CYCLE = [Severity.CRITICAL.value, Severity.WARNING.value, Severity.INFO.value]


async def severity_view(session: AsyncSession, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    row = await session.get(AppSetting, SEVERITY_OVERRIDES_SETTING)
    overrides = row.value if row and isinstance(row.value, dict) else {}
    rows = []
    for alert_type in AlertType:
        value = overrides.get(alert_type.value, DEFAULT_SEVERITY[alert_type].value)
        marker = " ✏️" if alert_type.value in overrides else ""
        rows.append([_btn(f"{type_label(alert_type.value, lang)} → {value.upper()}{marker}", "sevset", key=alert_type.value)])
    rows.append([_btn(t("common.back", lang), "panel")])
    return t("admin.severity_title", lang), InlineKeyboardMarkup(inline_keyboard=rows)


async def cycle_severity(session: AsyncSession, actor: User, alert_type: str) -> None:
    row = await session.get(AppSetting, SEVERITY_OVERRIDES_SETTING)
    overrides = dict(row.value) if row and isinstance(row.value, dict) else {}
    default = DEFAULT_SEVERITY[AlertType(alert_type)].value
    current = overrides.get(alert_type, default)
    following = SEVERITY_CYCLE[(SEVERITY_CYCLE.index(current) + 1) % len(SEVERITY_CYCLE)]
    if following == default:
        overrides.pop(alert_type, None)
    else:
        overrides[alert_type] = following
    if row is None:
        session.add(AppSetting(key=SEVERITY_OVERRIDES_SETTING, value=overrides))
    else:
        row.value = overrides
    session.add(AuditLog(actor_id=actor.id, action="severity_override", target=alert_type, details={"severity": following}))
    await session.commit()


# --- Callback'lar ---

@router.callback_query(AdminNav.filter())
async def cb_admin(
    callback: CallbackQuery, callback_data: AdminNav, session: AsyncSession, user: User, lang: str, state: FSMContext
) -> None:
    section = callback_data.section
    permissions = await user_permissions(user)
    required = dict(ADMIN_SECTIONS).get(section) or {
        "setval": Permission.MANAGE_SETTINGS, "sevset": Permission.MANAGE_SETTINGS,
    }.get(section)
    allowed = (
        (section == "panel" and any(p in permissions for p in ADMIN_PERMISSIONS))
        or (section == "backup" and user.role == UserRole.OWNER)
        or (required is not None and required in permissions)
    )
    if not allowed:
        await callback.answer(t("access.no_permission", lang), show_alert=True)
        return

    message = callback.message
    if section == "panel":
        await message.edit_text(t("admin.title", lang), reply_markup=await admin_panel(user, lang))
    elif section == "users":
        text, markup = await users_list_view(session, 0, lang)
        await message.edit_text(text, reply_markup=markup)
    elif section == "groups":
        await show_groups(message, session, lang)
    elif section == "depots":
        await show_depots(message, session, lang)
    elif section == "broadcast":
        await start_broadcast(message, state, lang)
    elif section == "status":
        await message.edit_text(await status_text(session, user, lang), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_btn("🔄", "status"), _btn(t("common.back", lang), "panel")]]))
    elif section == "audit":
        text, markup = await audit_view(session, user, lang, callback_data.page)
        await message.edit_text(text, reply_markup=markup)
    elif section == "settings":
        text, markup = await settings_view(session, lang, callback_data.key)
        await message.edit_text(text, reply_markup=markup)
    elif section == "setval":
        try:
            await app_settings.apply_choice(session, user, callback_data.key, callback_data.value or "")
        except (KeyError, ValueError):
            await callback.answer(t("settings.invalid", lang), show_alert=True)
            return
        text, markup = await settings_view(session, lang)
        await message.edit_text(text, reply_markup=markup)
    elif section == "severity":
        text, markup = await severity_view(session, lang)
        await message.edit_text(text, reply_markup=markup)
    elif section == "sevset":
        if callback_data.key not in {a.value for a in AlertType}:
            await callback.answer(t("settings.invalid", lang), show_alert=True)
            return
        await cycle_severity(session, user, callback_data.key)
        text, markup = await severity_view(session, lang)
        await message.edit_text(text, reply_markup=markup)
    elif section == "backup":
        await send_backup(message, session, user, lang)
    await callback.answer()


async def send_backup(target: Message, session: AsyncSession, user: User, lang: str) -> None:
    backup = latest_backup(settings.backup_dir)
    if backup is None:
        await target.answer(t("backup.none", lang))
        return
    if backup.size > TELEGRAM_UPLOAD_LIMIT:
        await target.answer(t("backup.too_big", lang, size=f"{backup.size / 1024 / 1024:.0f}", name=backup.path.name))
        return
    session.add(AuditLog(actor_id=user.id, action="backup_downloaded", target=backup.path.name))
    await session.commit()
    await target.answer_document(
        FSInputFile(backup.path),
        caption=t("backup.caption", lang, time=format_time(backup.created_at, user.timezone), size=f"{backup.size / 1024:.0f}"),
    )
