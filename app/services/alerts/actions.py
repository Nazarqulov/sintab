import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.keyboards.alerts import AlertActionType, alert_keyboard
from app.core.permissions import Permission
from app.db.models import Alert, AlertMute, AlertStatus, AuditLog, DeliveryStatus, User
from app.services.alerts.formatter import TYPE_LABELS, format_alert
from app.services.users import has_permission

logger = logging.getLogger(__name__)

MUTE_DURATION = timedelta(hours=1)


class ActionError(Exception):
    """Foydalanuvchiga ko'rsatiladigan rad javobi."""


async def apply_action(session: AsyncSession, alert_id: int, action: AlertActionType, user: User | None) -> str:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise ActionError("Alert topilmadi.")

    now = datetime.now(UTC)
    if action == AlertActionType.ACK:
        await _require(user, Permission.ACK_ALERTS)
        if alert.status != AlertStatus.NEW:
            raise ActionError("Bu alert allaqachon qabul qilingan.")
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_by_user = user
        alert.acknowledged_at = now
        reply = "✅ Qabul qilindi"

    elif action == AlertActionType.ASSIGN:
        await _require(user, Permission.ACK_ALERTS)
        if alert.status == AlertStatus.RESOLVED:
            raise ActionError("Alert yopilgan.")
        if alert.status == AlertStatus.NEW:
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_by_user = user
            alert.acknowledged_at = now
        alert.assigned_to_user = user
        alert.assigned_at = now
        reply = "👤 Alert sizga biriktirildi"

    elif action == AlertActionType.RESOLVE:
        await _require(user, Permission.RESOLVE_ALERTS)
        if alert.status == AlertStatus.RESOLVED:
            raise ActionError("Bu alert allaqachon yopilgan.")
        if alert.status != AlertStatus.ACKNOWLEDGED:
            raise ActionError("Avval alertni qabul qiling.")
        alert.status = AlertStatus.RESOLVED
        alert.resolved_by_user = user
        alert.resolved_at = now
        reply = "✔️ Alert yopildi"

    elif action == AlertActionType.MUTE:
        await _require(user, Permission.ACK_ALERTS)
        if alert.vehicle_id is None:
            raise ActionError("Mashina noma'lum — mute qilib bo'lmaydi.")
        session.add(
            AlertMute(
                vehicle_id=alert.vehicle_id,
                alert_type=alert.alert_type,
                muted_until=now + MUTE_DURATION,
                muted_by=user.id,
            )
        )
        type_label = TYPE_LABELS.get(alert.alert_type, alert.alert_type)
        reply = f"🔕 Bu mashinaning «{type_label}» alertlari 1 soatga o'chirildi"

    else:
        raise ActionError("Noma'lum amal.")

    session.add(AuditLog(actor_id=user.id, action=f"alert_{action.value}", target=f"alert:{alert.id}"))
    await session.commit()
    return reply


async def _require(user: User | None, permission: Permission) -> None:
    if not await has_permission(user, permission):
        raise ActionError("⛔ Sizda bu amal uchun ruxsat yo'q.")


async def refresh_alert_messages(bot: Bot, session: AsyncSession, alert_id: int, tz_name: str) -> None:
    """Alert yuborilgan barcha chatlardagi xabarni yangi holat bilan tahrirlaydi."""
    alert = await session.scalar(
        select(Alert)
        .options(selectinload(Alert.deliveries))
        .where(Alert.id == alert_id)
        .execution_options(populate_existing=True)
    )
    if alert is None:
        return
    text = format_alert(alert, tz_name)
    markup = alert_keyboard(alert)
    for delivery in alert.deliveries:
        if delivery.status != DeliveryStatus.SENT or delivery.message_id is None:
            continue
        for _ in range(2):
            try:
                await bot.edit_message_text(
                    text=text,
                    chat_id=delivery.chat_id,
                    message_id=delivery.message_id,
                    reply_markup=markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                break
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error):
                    logger.warning("Xabar %s/%s tahrirlanmadi: %s", delivery.chat_id, delivery.message_id, error)
                break
