"""/reports (davr + CSV/Excel/PDF eksport) va /stats (tezkor holat)."""

from datetime import UTC, datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.menu import home_button
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import Alert, AlertDelivery, AlertStatus, AuditLog, DeliveryStatus, Group, User, UserStatus
from app.services import reports as svc
from app.services.alerts.formatter import safe_zone
from app.services.users import has_permission

router = Router(name="reports")

VIEW_REPORTS = {"permission": Permission.VIEW_REPORTS}
FORMATS = {"xlsx": "📊 Excel", "pdf": "📄 PDF", "csv": "🧾 CSV"}


class ReportAction(CallbackData, prefix="rp"):
    period: str = "today"
    fmt: str | None = None


def _period_label(period: str, lang: str, user: User) -> str:
    start, end = svc.period_range(period, user.timezone, datetime.now(UTC))
    if period in ("today", "yesterday"):
        return f"{t(f'reports.period.{period}', lang)} ({start:%d.%m.%Y})"
    return f"{t(f'reports.period.{period}', lang)} ({start:%d.%m}–{end:%d.%m.%Y})"


async def _markup(user: User, lang: str, period: str) -> InlineKeyboardMarkup:
    periods = [
        InlineKeyboardButton(text=f"{'• ' if p == period else ''}{t(f'reports.period.{p}', lang)}", callback_data=ReportAction(period=p).pack())
        for p in svc.PERIODS
    ]
    rows = [periods[:2], periods[2:]]
    if await has_permission(user, Permission.EXPORT_REPORTS):
        rows.append([InlineKeyboardButton(text=label, callback_data=ReportAction(period=period, fmt=fmt).pack()) for fmt, label in FORMATS.items()])
    rows.append([home_button(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def report_view(session: AsyncSession, user: User, lang: str, period: str) -> tuple[str, InlineKeyboardMarkup]:
    start, end = svc.period_range(period, user.timezone, datetime.now(UTC))
    data = svc.build_report(await svc.load_alerts(session, user, start, end))
    return svc.format_report(data, lang, _period_label(period, lang, user)), await _markup(user, lang, period)


async def show_reports(target: Message, session: AsyncSession, user: User, lang: str) -> None:
    text, markup = await report_view(session, user, lang, "today")
    await target.answer(text, reply_markup=markup)


@router.message(Command("reports"), flags=VIEW_REPORTS)
async def cmd_reports(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await show_reports(message, session, user, lang)


@router.callback_query(ReportAction.filter(), flags=VIEW_REPORTS)
async def cb_report(callback: CallbackQuery, callback_data: ReportAction, session: AsyncSession, user: User, lang: str) -> None:
    period = callback_data.period if callback_data.period in svc.PERIODS else "today"
    if callback_data.fmt is None:
        text, markup = await report_view(session, user, lang, period)
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()
        return

    if not await has_permission(user, Permission.EXPORT_REPORTS) or callback_data.fmt not in FORMATS:
        await callback.answer(t("access.no_permission", lang), show_alert=True)
        return
    await callback.answer(t("reports.preparing", lang))
    start, end = svc.period_range(period, user.timezone, datetime.now(UTC))
    alerts = await svc.load_alerts(session, user, start, end)
    data = svc.build_report(alerts)
    rows = svc.export_rows(alerts, user.timezone, lang)
    title = f"{t('reports.file_title', lang)} — {_period_label(period, lang, user)}"
    stamp = f"{start:%Y%m%d}-{end:%Y%m%d}"
    if callback_data.fmt == "xlsx":
        content, filename = svc.to_xlsx(rows, data, lang, title), f"alerts_{stamp}.xlsx"
    elif callback_data.fmt == "pdf":
        content, filename = svc.to_pdf(rows, data, lang, title), f"alerts_{stamp}.pdf"
    else:
        content, filename = svc.to_csv(rows, lang), f"alerts_{stamp}.csv"
    session.add(AuditLog(actor_id=user.id, action="report_export", details={"format": callback_data.fmt, "period": period, "rows": len(rows)}))
    await session.commit()
    await callback.message.answer_document(BufferedInputFile(content, filename=filename), caption=title)


async def show_stats(target: Message, session: AsyncSession, user: User, lang: str) -> None:
    now = datetime.now(UTC)
    tz, _ = safe_zone(user.timezone)
    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    alerts = await svc.load_alerts(session, user, midnight, now + timedelta(seconds=1))
    data = svc.build_report(alerts)
    open_new = await session.scalar(select(func.count(Alert.id)).where(Alert.status == AlertStatus.NEW, Alert.suppressed.is_(False))) or 0
    failed = await session.scalar(
        select(func.count(AlertDelivery.id)).where(
            AlertDelivery.status == DeliveryStatus.FAILED, AlertDelivery.created_at >= now - timedelta(hours=24)
        )
    ) or 0
    users = dict((await session.execute(select(User.status, func.count()).group_by(User.status))).all())
    groups = await session.scalar(select(func.count(Group.id))) or 0
    text = t(
        "stats.text",
        lang,
        today=data.total,
        critical=data.by_severity.get("critical", 0),
        warning=data.by_severity.get("warning", 0),
        info=data.by_severity.get("info", 0),
        open_new=open_new,
        open_critical=data.open_critical,
        avg=svc.format_minutes(data.avg_response_min),
        failed=failed,
        active=users.get(UserStatus.ACTIVE, 0),
        pending=users.get(UserStatus.PENDING, 0),
        groups=groups,
    )
    await target.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[home_button(lang)]]))


@router.message(Command("stats"), flags=VIEW_REPORTS)
async def cmd_stats(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await show_stats(message, session, user, lang)
