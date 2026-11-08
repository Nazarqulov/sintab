"""Logistika fon vazifalari: HOS va yoqilg'i ogohlantirishlari, hujjat muddatlari, kunlik smena xulosasi.

HOS/yoqilg'i ogohlantirishlari oddiy alert sifatida yaratiladi — routing, tugmalar, eskalatsiya va
hisobotlar ular uchun ham avtomatik ishlaydi. Takrorlanmaslik eventId orqali (kuniga bir marta).
"""

import logging
from datetime import datetime

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.notify import send_safe
from app.core.config import settings
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import Document, DocumentOwner, User, UserRole, UserStatus, Vehicle
from app.services import reports
from app.services.alerts.formatter import safe_zone
from app.services.alerts.ingest import ingest_alert
from app.services.alerts.types import AlertType
from app.services.app_settings import get_setting
from app.services.documents import owner_label
from app.services.monitoring import mark_once
from app.services.samsara.client import SamsaraClient
from app.services.samsara.hos import format_duration, parse_hos_clock
from app.services.samsara.live import parse_vehicle_stats
from app.services.samsara.parser import NormalizedAlert
from app.services.users import has_permission

logger = logging.getLogger(__name__)


def _local_date(now: datetime):
    tz, _ = safe_zone(settings.default_timezone)
    return now.astimezone(tz).date()


async def run_hos_warnings(session: AsyncSession, client: SamsaraClient, now: datetime) -> list[int]:
    threshold = await get_setting(session, "hos_warning_minutes")
    if not threshold:
        return []
    delivery_ids: list[int] = []
    for item in await client.get_hos_clocks():
        clock = parse_hos_clock(item)
        if clock is None or not clock.is_working or clock.tightest_ms is None:
            continue
        if clock.tightest_ms > threshold * 60_000:
            continue
        data = NormalizedAlert(
            event_id=f"hos-warning:{clock.driver_samsara_id}:{_local_date(now).isoformat()}",
            alert_type=AlertType.HOS_WARNING.value,
            description=(
                f"HOS: {format_duration(clock.tightest_ms)} left "
                f"(drive {format_duration(clock.drive_remaining_ms)}, shift {format_duration(clock.shift_remaining_ms)}, "
                f"cycle {format_duration(clock.cycle_remaining_ms)})"
            ),
            happened_at=now,
            driver_samsara_id=clock.driver_samsara_id,
            driver_name=clock.driver_name,
            raw=item,
        )
        result = await ingest_alert(session, data, owner_chat_id=settings.owner_telegram_id, now=now)
        delivery_ids += result.delivery_ids
    return delivery_ids


async def run_fuel_check(session: AsyncSession, client: SamsaraClient, now: datetime) -> list[int]:
    """Yoqilg'i va joylashuvni yangilaydi; chegaradan past bo'lsa — kuniga bitta low_fuel alert."""
    threshold = await get_setting(session, "fuel_warning_percent")
    vehicles = {v.samsara_id: v for v in (await session.scalars(select(Vehicle))).all()}
    low: list[tuple[Vehicle, dict, float]] = []
    for item in await client.get_vehicle_stats(types=("fuelPercents", "gps")):
        vehicle = vehicles.get(str(item.get("id")))
        if vehicle is None:
            continue
        status = parse_vehicle_stats(item)
        if status.fuel_percent is not None:
            vehicle.fuel_percent = status.fuel_percent
            vehicle.fuel_updated_at = now
            if threshold and status.fuel_percent < threshold:
                low.append((vehicle, item, status.fuel_percent))
        if status.latitude is not None:
            vehicle.last_location = {"latitude": status.latitude, "longitude": status.longitude, "address": status.address}
            vehicle.last_seen_at = status.gps_time or now
    await session.commit()

    delivery_ids: list[int] = []
    for vehicle, item, percent in low:
        data = NormalizedAlert(
            event_id=f"low-fuel:{vehicle.samsara_id}:{_local_date(now).isoformat()}",
            alert_type=AlertType.LOW_FUEL.value,
            description=f"Fuel level {percent:.0f}% (threshold {threshold}%)",
            happened_at=now,
            vehicle_samsara_id=vehicle.samsara_id,
            vehicle_name=vehicle.name,
            latitude=(vehicle.last_location or {}).get("latitude"),
            longitude=(vehicle.last_location or {}).get("longitude"),
            address=(vehicle.last_location or {}).get("address"),
            raw=item,
        )
        result = await ingest_alert(session, data, owner_chat_id=settings.owner_telegram_id, now=now)
        delivery_ids += result.delivery_ids
    return delivery_ids


async def _leaders(session: AsyncSession) -> list[User]:
    return list((await session.scalars(
        select(User).where(User.status == UserStatus.ACTIVE, User.role.in_([UserRole.OWNER, UserRole.ADMIN]))
    )).all())


async def run_document_reminders(session: AsyncSession, bot: Bot, now: datetime) -> int:
    """Muddati 30/14/7/1 kun qolgan va o'tib ketgan hujjatlar haqida bir martadan eslatadi."""
    thresholds = sorted(set(await get_setting(session, "doc_reminder_days")), reverse=True)
    today = _local_date(now)
    documents = (await session.scalars(select(Document).where(Document.expires_at.is_not(None)))).all()
    sent = 0
    for document in documents:
        days_left = (document.expires_at - today).days
        stage = "expired" if days_left < 0 else next((str(d) for d in reversed(thresholds) if days_left <= d), None)
        if stage is None or not await mark_once(session, f"doc:{document.id}:{stage}"):
            continue
        recipients = await _leaders(session)
        if document.owner_type == DocumentOwner.DRIVER.value and document.owner_id:
            driver_user = await session.scalar(
                select(User).where(User.driver_id == document.owner_id, User.status == UserStatus.ACTIVE)
            )
            if driver_user is not None:
                recipients.append(driver_user)
        owner = await owner_label(session, document.owner_type, document.owner_id)
        for user in {u.id: u for u in recipients}.values():
            key = "docs.reminder_expired" if stage == "expired" else "docs.reminder_expiring"
            text = t(
                key, user.language,
                title=document.title, type=t(f"docs.type.{document.doc_type}", user.language),
                owner=owner, days=max(days_left, 0), date=document.expires_at.strftime("%d.%m.%Y"),
            )
            if await send_safe(bot, user.telegram_id, text):
                sent += 1
    return sent


async def run_shift_report(session: AsyncSession, bot: Bot, now: datetime) -> int:
    """Belgilangan soatdan keyin kuniga bir marta menejerlarga bugungi xulosa."""
    hour = await get_setting(session, "shift_report_hour")
    tz, _ = safe_zone(settings.default_timezone)
    local = now.astimezone(tz)
    if hour is None or hour < 0 or local.hour < hour:
        return 0
    if not await mark_once(session, f"shift-report:{local.date().isoformat()}"):
        return 0
    recipients = (await session.scalars(
        select(User).where(
            User.status == UserStatus.ACTIVE, User.role.in_([UserRole.OWNER, UserRole.ADMIN, UserRole.MANAGER])
        )
    )).all()
    sent = 0
    for user in recipients:
        if not await has_permission(user, Permission.VIEW_REPORTS):
            continue
        start, end = reports.period_range("today", user.timezone, now)
        data = reports.build_report(await reports.load_alerts(session, user, start, end))
        text = f"{t('shift.title', user.language)}\n\n" + reports.format_report(
            data, user.language, f"{t('reports.period.today', user.language)} ({start:%d.%m.%Y})"
        )
        if await send_safe(bot, user.telegram_id, text):
            sent += 1
    return sent

