"""Fon vazifalari: critical alert eskalatsiyasi va tungi digest."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.i18n import t
from app.core.timeutils import hour_in_window
from app.db.models import (
    Alert,
    AlertDelivery,
    AlertStatus,
    DeliveryKind,
    DeliveryStatus,
    Group,
    User,
    UserRole,
    UserStatus,
)
from app.services.alerts.formatter import as_utc, safe_zone, type_label, vehicle_label
from app.services.alerts.render import delivery_context
from app.services.alerts.types import Severity
from app.services.app_settings import get_setting

logger = logging.getLogger(__name__)

ESCALATION_LOOKBACK = timedelta(hours=24)
DIGEST_MAX_LINES = 15


async def run_escalations(session: AsyncSession, now: datetime) -> list[int]:
    """N daqiqa ichida hech kim qabul qilmagan critical alertlarni eskalatsiya guruhlari va Owner/Admin'larga yuboradi."""
    minutes = await get_setting(session, "escalation_minutes")
    if not minutes:
        return []
    alerts = (
        await session.scalars(
            select(Alert).where(
                Alert.severity == Severity.CRITICAL.value,
                Alert.status == AlertStatus.NEW,
                Alert.escalated_at.is_(None),
                Alert.suppressed.is_(False),
                Alert.created_at <= now - timedelta(minutes=minutes),
                Alert.created_at >= now - ESCALATION_LOOKBACK,
            )
        )
    ).all()
    if not alerts:
        return []

    groups = (await session.scalars(select(Group).where(Group.is_escalation.is_(True)))).all()
    leaders = (
        await session.scalars(
            select(User).where(User.status == UserStatus.ACTIVE, User.role.in_([UserRole.OWNER, UserRole.ADMIN]))
        )
    ).all()
    targets: list[tuple[int | None, int, int | None]] = [(g.id, g.chat_id, g.topic_id) for g in groups]
    targets += [(None, u.telegram_id, None) for u in leaders]
    targets = list({(chat_id, topic_id): (gid, chat_id, topic_id) for gid, chat_id, topic_id in targets}.values())

    deliveries: list[AlertDelivery] = []
    for alert in alerts:
        alert.escalated_at = now
        deliveries += [
            AlertDelivery(alert_id=alert.id, group_id=gid, chat_id=chat_id, topic_id=topic_id, kind=DeliveryKind.ESCALATION.value)
            for gid, chat_id, topic_id in targets
        ]
        logger.warning("Alert %s eskalatsiya qilindi (%s daqiqa qabul qilinmadi)", alert.id, minutes)
    session.add_all(deliveries)
    await session.commit()
    return [d.id for d in deliveries]


async def run_digest(session: AsyncSession, bot: Bot, now: datetime) -> int:
    """Digest oynasidan tashqarida bo'lsak — qoldirilgan alertlarni har chatga bitta xulosa qilib yuboradi."""
    tz, _ = safe_zone(settings.default_timezone)
    start = await get_setting(session, "digest_start_hour")
    end = await get_setting(session, "digest_end_hour")
    if await get_setting(session, "digest_enabled") and hour_in_window(now.astimezone(tz).hour, start, end):
        return 0

    deferred = (
        await session.scalars(
            select(AlertDelivery)
            .options(selectinload(AlertDelivery.alert))
            .where(AlertDelivery.status == DeliveryStatus.DEFERRED)
            .order_by(AlertDelivery.id)
        )
    ).all()
    by_chat: dict[tuple[int, int | None], list[AlertDelivery]] = defaultdict(list)
    for delivery in deferred:
        by_chat[(delivery.chat_id, delivery.topic_id)].append(delivery)

    sent = 0
    for (chat_id, topic_id), items in by_chat.items():
        context = await delivery_context(session, items[0])
        text = format_digest([d.alert for d in items], context.lang, context.tz, start, end)
        try:
            await bot.send_message(chat_id=chat_id, text=text, message_thread_id=topic_id)
            status, error = DeliveryStatus.DIGESTED, None
            sent += 1
        except TelegramAPIError as exc:
            status, error = DeliveryStatus.FAILED, str(exc)[:500]
            logger.error("Digest chat %s ga yuborilmadi: %s", chat_id, exc)
        for delivery in items:
            delivery.status = status
            delivery.last_error = error
            delivery.sent_at = now
        await session.commit()
    return sent


def format_digest(alerts: list[Alert], lang: str, tz_name: str, start: int, end: int) -> str:
    tz, _ = safe_zone(tz_name)
    counts: dict[str, int] = defaultdict(int)
    for alert in alerts:
        counts[alert.alert_type] += 1
    lines = [t("digest.title", lang, count=len(alerts), start=f"{start:02d}:00", end=f"{end:02d}:00"), ""]
    lines += [f"{type_label(kind, lang)}: <b>{n}</b>" for kind, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    lines.append("")
    ordered = sorted(alerts, key=lambda a: as_utc(a.happened_at or a.created_at))
    for alert in ordered[:DIGEST_MAX_LINES]:
        when = as_utc(alert.happened_at or alert.created_at).astimezone(tz)
        lines.append(f"• {when:%H:%M} · {escape(vehicle_label(alert))} · {type_label(alert.alert_type, lang)}")
    if len(ordered) > DIGEST_MAX_LINES:
        lines.append(t("digest.more", lang, count=len(ordered) - DIGEST_MAX_LINES))
    return "\n".join(lines)
