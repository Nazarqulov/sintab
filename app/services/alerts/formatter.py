"""Alert xabari matni (Telegram HTML), foydalanuvchi/guruh tilida."""

from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.i18n import t
from app.db.models import Alert, AlertStatus
from app.services.alerts.types import Severity
from app.services.users import display_name

SEVERITY_ICONS = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.INFO: "🟢"}
RESOLUTION_REASONS = ("driver_contacted", "false_alarm", "mechanic_sent", "customer_informed", "no_action", "other")


def maps_url(latitude: float, longitude: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def safe_zone(tz_name: str | None) -> tuple[ZoneInfo, str]:
    try:
        return ZoneInfo(tz_name or "UTC"), tz_name or "UTC"
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC"), "UTC"


def format_time(value: datetime | None, tz_name: str) -> str:
    if value is None:
        return "—"
    tz, tz_name = safe_zone(tz_name)
    return f"{as_utc(value).astimezone(tz):%d.%m.%Y %H:%M} ({tz_name})"


def type_label(alert_type: str, lang: str | None) -> str:
    label = t(f"alert.type.{alert_type}", lang)
    return label if label != f"alert.type.{alert_type}" else t("alert.type.other", lang)


def severity_label(severity: str) -> str:
    icon = SEVERITY_ICONS.get(severity, "🟢")
    return f"{icon} <b>{escape(severity.upper())}</b>"


def vehicle_label(alert: Alert) -> str:
    vehicle = alert.vehicle
    if vehicle is None:
        return "—"
    label = vehicle.name or vehicle.samsara_id
    return f"{label} ({vehicle.plate})" if vehicle.plate else label


def format_alert(alert: Alert, tz_name: str, lang: str | None = None, *, escalation_minutes: int | None = None) -> str:
    lines: list[str] = []
    if escalation_minutes is not None and alert.status == AlertStatus.NEW:
        lines += [t("alert.escalated", lang, minutes=escalation_minutes), ""]
    lines.append(f"{severity_label(alert.severity)} · {type_label(alert.alert_type, lang)}")
    if alert.samsara_resolved:
        lines.append(t("alert.samsara_resolved", lang))
    if alert.message:
        lines.append(f"<i>{escape(alert.message)}</i>")
    lines.append("")

    driver = (alert.driver.full_name or alert.driver.samsara_id) if alert.driver else "—"
    lines.append(t("alert.vehicle", lang, vehicle=escape(vehicle_label(alert))))
    lines.append(t("alert.driver", lang, driver=escape(driver)))
    if alert.driver and alert.driver.phone:
        lines.append(f"📞 {escape(alert.driver.phone)}")
    lines.append(t("alert.time", lang, time=format_time(alert.happened_at or alert.created_at, tz_name)))
    if alert.latitude is not None and alert.longitude is not None:
        place = alert.address or f"{alert.latitude:.5f}, {alert.longitude:.5f}"
        url = escape(maps_url(alert.latitude, alert.longitude), quote=True)
        lines.append(t("alert.location", lang, url=url, place=escape(place)))
    if alert.samsara_url:
        lines.append(t("alert.samsara_link", lang, url=escape(alert.samsara_url, quote=True)))

    lines.append("")
    lines.extend(_status_lines(alert, tz_name, lang))
    return "\n".join(lines)


def _status_lines(alert: Alert, tz_name: str, lang: str | None) -> list[str]:
    if alert.status == AlertStatus.RESOLVED:
        lines = [
            t(
                "alert.status_resolved",
                lang,
                who=escape(display_name(alert.resolved_by_user)),
                time=format_time(alert.resolved_at, tz_name),
            )
        ]
        if alert.resolution_reason:
            lines.append(t("alert.reason_line", lang, reason=t(f"alert.reason.{alert.resolution_reason}", lang)))
        if alert.resolution_note:
            lines.append(f"📝 {escape(alert.resolution_note)}")
        return lines
    if alert.status == AlertStatus.ACKNOWLEDGED:
        lines = [
            t(
                "alert.status_ack",
                lang,
                who=escape(display_name(alert.acknowledged_by_user)),
                time=format_time(alert.acknowledged_at, tz_name),
            )
        ]
        if alert.assigned_to_user is not None:
            lines.append(t("alert.assignee", lang, who=escape(display_name(alert.assigned_to_user))))
        return lines
    return [t("alert.status_new", lang)]
