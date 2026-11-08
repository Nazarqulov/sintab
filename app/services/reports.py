"""Alert statistikasi va eksport (CSV / Excel / PDF). Foydalanuvchi faqat o'z ko'rish doirasidagi alertlarni oladi."""

import csv
import io
import logging
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import t
from app.db.models import Alert, AlertStatus, AuditLog, User
from app.services.alerts.formatter import as_utc, safe_zone, type_label
from app.services.scope import scoped_alerts
from app.services.users import display_name

logger = logging.getLogger(__name__)

PERIODS = ("today", "yesterday", "7d", "30d")
EXPORT_FORMATS = ("xlsx", "pdf", "csv")
PDF_MAX_ROWS = 400
FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
]


def period_range(period: str, tz_name: str, now: datetime) -> tuple[datetime, datetime]:
    """Foydalanuvchi vaqt zonasidagi kun chegaralari (UTC'da qaytadi)."""
    tz, _ = safe_zone(tz_name)
    local_midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start, end = local_midnight, now.astimezone(tz)
    elif period == "yesterday":
        start, end = local_midnight - timedelta(days=1), local_midnight
    elif period == "7d":
        start, end = local_midnight - timedelta(days=6), now.astimezone(tz)
    elif period == "30d":
        start, end = local_midnight - timedelta(days=29), now.astimezone(tz)
    else:
        raise ValueError(period)
    return start, end


@dataclass
class ReportData:
    total: int = 0
    by_severity: Counter = field(default_factory=Counter)
    by_type: Counter = field(default_factory=Counter)
    by_reason: Counter = field(default_factory=Counter)
    top_vehicles: list[tuple[str, int]] = field(default_factory=list)
    top_drivers: list[tuple[str, int]] = field(default_factory=list)
    acknowledged: int = 0
    resolved: int = 0
    open_critical: int = 0
    avg_response_min: float | None = None
    median_response_min: float | None = None
    avg_resolve_min: float | None = None


async def load_alerts(session: AsyncSession, user: User, start: datetime, end: datetime) -> list[Alert]:
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    stmt = (await scoped_alerts(user)).where(Alert.created_at >= start, Alert.created_at < end).order_by(Alert.created_at)
    return list((await session.scalars(stmt)).unique().all())


def build_report(alerts: list[Alert]) -> ReportData:
    data = ReportData(total=len(alerts))
    vehicles: Counter = Counter()
    drivers: Counter = Counter()
    responses: list[float] = []
    resolves: list[float] = []
    for alert in alerts:
        data.by_severity[alert.severity] += 1
        data.by_type[alert.alert_type] += 1
        if alert.vehicle:
            vehicles[alert.vehicle.name or alert.vehicle.samsara_id] += 1
        if alert.driver:
            drivers[alert.driver.full_name or alert.driver.samsara_id] += 1
        if alert.acknowledged_at:
            data.acknowledged += 1
            responses.append((as_utc(alert.acknowledged_at) - as_utc(alert.created_at)).total_seconds() / 60)
        if alert.status == AlertStatus.RESOLVED and alert.resolved_at:
            data.resolved += 1
            resolves.append((as_utc(alert.resolved_at) - as_utc(alert.created_at)).total_seconds() / 60)
            data.by_reason[alert.resolution_reason or "other"] += 1
        if alert.severity == "critical" and alert.status == AlertStatus.NEW:
            data.open_critical += 1
    data.top_vehicles = vehicles.most_common(5)
    data.top_drivers = drivers.most_common(5)
    if responses:
        data.avg_response_min = sum(responses) / len(responses)
        data.median_response_min = statistics.median(responses)
    if resolves:
        data.avg_resolve_min = sum(resolves) / len(resolves)
    return data


def format_minutes(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f} min" if value < 120 else f"{value / 60:.1f} h"


def format_report(data: ReportData, lang: str, period_label: str) -> str:
    lines = [t("reports.title", lang, period=period_label), ""]
    if not data.total:
        return "\n".join(lines + [t("reports.empty", lang)])
    lines.append(
        t(
            "reports.summary",
            lang,
            total=data.total,
            critical=data.by_severity.get("critical", 0),
            warning=data.by_severity.get("warning", 0),
            info=data.by_severity.get("info", 0),
            acked=data.acknowledged,
            resolved=data.resolved,
            open_critical=data.open_critical,
            avg=format_minutes(data.avg_response_min),
            median=format_minutes(data.median_response_min),
            resolve=format_minutes(data.avg_resolve_min),
        )
    )
    lines += ["", t("reports.by_type", lang)]
    lines += [f"  {type_label(kind, lang)}: <b>{n}</b>" for kind, n in data.by_type.most_common(8)]
    if data.top_vehicles:
        lines += ["", t("reports.top_vehicles", lang)]
        lines += [f"  {i}. {name} — <b>{n}</b>" for i, (name, n) in enumerate(data.top_vehicles, 1)]
    if data.top_drivers:
        lines += ["", t("reports.top_drivers", lang)]
        lines += [f"  {i}. {name} — <b>{n}</b>" for i, (name, n) in enumerate(data.top_drivers, 1)]
    if data.by_reason:
        lines += ["", t("reports.by_reason", lang)]
        lines += [f"  {t(f'alert.reason.{r}', lang)}: <b>{n}</b>" for r, n in data.by_reason.most_common()]
    return "\n".join(lines)


EXPORT_COLUMNS = [
    "id", "time", "type", "severity", "vehicle", "plate", "driver", "status",
    "acknowledged_by", "response_min", "resolved_by", "resolve_min", "reason", "notes", "address", "samsara_url",
]


def export_rows(alerts: list[Alert], tz_name: str, lang: str) -> list[dict]:
    tz, _ = safe_zone(tz_name)
    rows = []
    for a in alerts:
        created = as_utc(a.created_at)
        happened = as_utc(a.happened_at or a.created_at).astimezone(tz)
        response = (as_utc(a.acknowledged_at) - created).total_seconds() / 60 if a.acknowledged_at else None
        resolve = (as_utc(a.resolved_at) - created).total_seconds() / 60 if a.resolved_at else None
        rows.append(
            {
                "id": a.id,
                "time": happened.strftime("%Y-%m-%d %H:%M"),
                "type": type_label(a.alert_type, lang).split(" ", 1)[-1],
                "severity": a.severity.upper(),
                "vehicle": a.vehicle.name if a.vehicle else "",
                "plate": (a.vehicle.plate or "") if a.vehicle else "",
                "driver": (a.driver.full_name or "") if a.driver else "",
                "status": a.status.value,
                "acknowledged_by": display_name(a.acknowledged_by_user) if a.acknowledged_by_user else "",
                "response_min": round(response, 1) if response is not None else "",
                "resolved_by": display_name(a.resolved_by_user) if a.resolved_by_user else "",
                "resolve_min": round(resolve, 1) if resolve is not None else "",
                "reason": t(f"alert.reason.{a.resolution_reason}", lang).split(" ", 1)[-1] if a.resolution_reason else "",
                "notes": a.resolution_note or "",
                "address": a.address or "",
                "samsara_url": a.samsara_url or "",
            }
        )
    return rows


def column_titles(lang: str) -> list[str]:
    return [t(f"reports.col.{c}", lang) for c in EXPORT_COLUMNS]


def to_csv(rows: list[dict], lang: str) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(column_titles(lang))
    for row in rows:
        writer.writerow([row[c] for c in EXPORT_COLUMNS])
    # BOM — Excel UTF-8 CSV'ni (kirill/o'zbek harflari) to'g'ri ochishi uchun.
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(rows: list[dict], data: ReportData, lang: str, title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = t("reports.sheet_alerts", lang)[:31]
    header_fill = PatternFill("solid", fgColor="1F4E78")
    sheet.append(column_titles(lang))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    severity_fill = {"CRITICAL": "F8CBAD", "WARNING": "FFE699"}
    for row in rows:
        sheet.append([row[c] for c in EXPORT_COLUMNS])
        fill = severity_fill.get(row["severity"])
        if fill:
            sheet.cell(row=sheet.max_row, column=EXPORT_COLUMNS.index("severity") + 1).fill = PatternFill("solid", fgColor=fill)
    widths = {"id": 7, "time": 17, "type": 22, "severity": 11, "vehicle": 18, "plate": 11, "driver": 20, "status": 13,
              "acknowledged_by": 18, "response_min": 10, "resolved_by": 18, "resolve_min": 10, "reason": 22,
              "notes": 40, "address": 36, "samsara_url": 30}
    for index, column in enumerate(EXPORT_COLUMNS, 1):
        sheet.column_dimensions[get_column_letter(index)].width = widths[column]
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    summary = workbook.create_sheet(t("reports.sheet_summary", lang)[:31])
    summary.append([title])
    summary["A1"].font = Font(bold=True, size=14)
    summary.append([])
    for label, value in [
        (t("reports.k_total", lang), data.total),
        ("CRITICAL", data.by_severity.get("critical", 0)),
        ("WARNING", data.by_severity.get("warning", 0)),
        ("INFO", data.by_severity.get("info", 0)),
        (t("reports.k_acked", lang), data.acknowledged),
        (t("reports.k_resolved", lang), data.resolved),
        (t("reports.k_avg_response", lang), format_minutes(data.avg_response_min)),
        (t("reports.k_median_response", lang), format_minutes(data.median_response_min)),
        (t("reports.k_avg_resolve", lang), format_minutes(data.avg_resolve_min)),
    ]:
        summary.append([label, value])
    summary.append([])
    summary.append([t("reports.k_top_vehicles", lang)])
    for name, n in data.top_vehicles:
        summary.append([name, n])
    summary.column_dimensions["A"].width = 36
    summary.column_dimensions["B"].width = 14

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _register_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in FONT_CANDIDATES:
        if path.exists():
            if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DejaVu", str(path)))
            return "DejaVu"
    logger.warning("DejaVu shrifti topilmadi — PDF'da kirill/o'zbek harflari buzilishi mumkin")
    return "Helvetica"


def to_pdf(rows: list[dict], data: ReportData, lang: str, title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = _register_font()
    title_style = ParagraphStyle("title", fontName=font, fontSize=16, leading=20, spaceAfter=6)
    body = ParagraphStyle("body", fontName=font, fontSize=9, leading=11)
    cell = ParagraphStyle("cell", fontName=font, fontSize=7, leading=8.5)

    story = [Paragraph(title, title_style)]
    summary = [
        [t("reports.k_total", lang), str(data.total), "CRITICAL", str(data.by_severity.get("critical", 0))],
        [t("reports.k_acked", lang), str(data.acknowledged), "WARNING", str(data.by_severity.get("warning", 0))],
        [t("reports.k_resolved", lang), str(data.resolved), "INFO", str(data.by_severity.get("info", 0))],
        [t("reports.k_avg_response", lang), format_minutes(data.avg_response_min),
         t("reports.k_avg_resolve", lang), format_minutes(data.avg_resolve_min)],
    ]
    summary_table = Table(summary, colWidths=[60 * mm, 30 * mm, 60 * mm, 30 * mm])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("BACKGROUND", (2, 0), (2, -1), colors.whitesmoke),
    ]))
    story += [summary_table, Spacer(1, 6 * mm)]

    if data.top_vehicles:
        top = ", ".join(f"{name} ({n})" for name, n in data.top_vehicles)
        story += [Paragraph(f"{t('reports.k_top_vehicles', lang)}: {top}", body), Spacer(1, 4 * mm)]

    columns = ["time", "type", "severity", "vehicle", "driver", "status", "acknowledged_by", "response_min", "reason"]
    titles = dict(zip(EXPORT_COLUMNS, column_titles(lang), strict=True))
    table_rows = [[Paragraph(titles[c], cell) for c in columns]]
    for row in rows[:PDF_MAX_ROWS]:
        table_rows.append([Paragraph(str(row[c]), cell) for c in columns])
    widths = [26, 34, 18, 30, 34, 20, 30, 16, 40]
    table = Table(table_rows, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    if len(rows) > PDF_MAX_ROWS:
        story += [Spacer(1, 3 * mm), Paragraph(t("reports.pdf_truncated", lang, shown=PDF_MAX_ROWS, total=len(rows)), body)]

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=10 * mm, bottomMargin=10 * mm,
        title=title,
    ).build(story)
    return buffer.getvalue()


def period_label(period: str, lang: str, tz_name: str, now: datetime) -> str:
    start, end = period_range(period, tz_name, now)
    if period in ("today", "yesterday"):
        return f"{t(f'reports.period.{period}', lang)} ({start:%d.%m.%Y})"
    return f"{t(f'reports.period.{period}', lang)} ({start:%d.%m}–{end:%d.%m.%Y})"


async def build_export(
    session: AsyncSession, user: User, period: str, fmt: str, lang: str, now: datetime
) -> tuple[bytes, str, str]:
    """Bot va Mini App uchun umumiy: (fayl, fayl nomi, sarlavha); eksport audit log'ga yoziladi."""
    start, end = period_range(period, user.timezone, now)
    alerts = await load_alerts(session, user, start, end)
    data = build_report(alerts)
    rows = export_rows(alerts, user.timezone, lang)
    title = f"{t('reports.file_title', lang)} — {period_label(period, lang, user.timezone, now)}"
    stamp = f"{start:%Y%m%d}-{end:%Y%m%d}"
    if fmt == "xlsx":
        content, filename = to_xlsx(rows, data, lang, title), f"alerts_{stamp}.xlsx"
    elif fmt == "pdf":
        content, filename = to_pdf(rows, data, lang, title), f"alerts_{stamp}.pdf"
    else:
        content, filename = to_csv(rows, lang), f"alerts_{stamp}.csv"
    session.add(AuditLog(actor_id=user.id, action="report_export", details={"format": fmt, "period": period, "rows": len(rows)}))
    await session.commit()
    return content, filename, title
