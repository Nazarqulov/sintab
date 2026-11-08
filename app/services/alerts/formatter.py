"""Alert xabari matni (Telegram HTML). Tarjimalar 4-bosqichda i18n'ga ko'chiriladi."""

from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db.models import Alert, AlertStatus
from app.services.alerts.types import AlertType, Severity
from app.services.users import display_name

TYPE_LABELS: dict[str, str] = {
    AlertType.CRASH: "💥 To'qnashuv",
    AlertType.PANIC_BUTTON: "🆘 Panic tugmasi",
    AlertType.HARSH_BRAKING: "🛑 Keskin tormoz",
    AlertType.HARSH_ACCELERATION: "💨 Keskin tezlanish",
    AlertType.HARSH_TURN: "↪️ Keskin burilish",
    AlertType.SPEEDING: "🏎 Tezlikni oshirish",
    AlertType.HOS_VIOLATION: "⏱ HOS buzilishi",
    AlertType.ENGINE_FAULT: "🔧 Dvigatel xatoligi",
    AlertType.LOW_FUEL: "⛽ Yoqilg'i kam",
    AlertType.MAINTENANCE_DUE: "🛠 Texnik xizmat muddati",
    AlertType.DEVICE_DISCONNECTED: "📡 Qurilma uzildi",
    AlertType.IDLE: "💤 Uzoq turib qolish (idle)",
    AlertType.GEOFENCE_ENTRY: "📍 Geozonaga kirish",
    AlertType.GEOFENCE_EXIT: "🚪 Geozonadan chiqish",
    AlertType.OTHER: "ℹ️ Alert",
}

SEVERITY_LABELS: dict[str, str] = {
    Severity.CRITICAL: "🔴 <b>CRITICAL</b>",
    Severity.WARNING: "🟠 <b>WARNING</b>",
    Severity.INFO: "🟢 <b>INFO</b>",
}


def maps_url(latitude: float, longitude: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def format_time(value: datetime | None, tz_name: str) -> str:
    if value is None:
        return "—"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz, tz_name = ZoneInfo("UTC"), "UTC"
    return f"{as_utc(value).astimezone(tz):%d.%m.%Y %H:%M} ({tz_name})"


def format_alert(alert: Alert, tz_name: str) -> str:
    severity = SEVERITY_LABELS.get(alert.severity, SEVERITY_LABELS[Severity.INFO])
    type_label = TYPE_LABELS.get(alert.alert_type, TYPE_LABELS[AlertType.OTHER])
    lines = [f"{severity} · {type_label}"]
    if alert.message:
        lines.append(f"<i>{escape(alert.message)}</i>")
    lines.append("")

    vehicle = alert.vehicle
    vehicle_label = "—"
    if vehicle is not None:
        vehicle_label = vehicle.name or vehicle.samsara_id
        if vehicle.plate:
            vehicle_label += f" ({vehicle.plate})"
    driver_label = (alert.driver.full_name or alert.driver.samsara_id) if alert.driver else "—"

    lines.append(f"🚚 Mashina: <b>{escape(vehicle_label)}</b>")
    lines.append(f"👤 Haydovchi: {escape(driver_label)}")
    lines.append(f"🕒 Vaqt: {format_time(alert.happened_at or alert.created_at, tz_name)}")
    if alert.latitude is not None and alert.longitude is not None:
        place = alert.address or f"{alert.latitude:.5f}, {alert.longitude:.5f}"
        url = maps_url(alert.latitude, alert.longitude)
        lines.append(f'📍 Joylashuv: <a href="{escape(url, quote=True)}">{escape(place)}</a>')
    if alert.samsara_url:
        lines.append(f'🔗 <a href="{escape(alert.samsara_url, quote=True)}">Samsara\'da ochish</a>')

    lines.append("")
    lines.extend(_status_lines(alert, tz_name))
    return "\n".join(lines)


def _status_lines(alert: Alert, tz_name: str) -> list[str]:
    if alert.status == AlertStatus.RESOLVED:
        who = escape(display_name(alert.resolved_by_user))
        return [f"📌 Holat: ✔️ Yopildi — {who} ({format_time(alert.resolved_at, tz_name)})"]
    if alert.status == AlertStatus.ACKNOWLEDGED:
        who = escape(display_name(alert.acknowledged_by_user))
        lines = [f"📌 Holat: ✅ Qabul qilindi — {who} ({format_time(alert.acknowledged_at, tz_name)})"]
        if alert.assigned_to_user is not None:
            lines.append(f"👷 Mas'ul: {escape(display_name(alert.assigned_to_user))}")
        return lines
    return ["📌 Holat: 🆕 Yangi"]
