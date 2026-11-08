import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.keyboards.alerts import AlertActionType
from app.core.i18n import LocalizedError, Text
from app.core.permissions import Permission
from app.db.models import Alert, AlertDelivery, AlertMute, AlertStatus, AuditLog, DeliveryStatus, User
from app.services.alerts.formatter import RESOLUTION_REASONS
from app.services.alerts.render import delivery_context, render_delivery
from app.services.users import display_name, has_permission

logger = logging.getLogger(__name__)

MUTE_DURATION = timedelta(hours=1)
MAX_NOTE_LENGTH = 2000


class ActionError(LocalizedError):
    """Foydalanuvchiga ko'rsatiladigan rad javobi (tarjima kaliti bilan)."""


async def _get_alert(session: AsyncSession, alert_id: int) -> Alert:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise ActionError("action_err.not_found")
    return alert


async def check_can_resolve(session: AsyncSession, alert_id: int, user: User | None) -> Alert:
    """"Yopish" bosilganda sabab tanlashdan oldin tekshirish."""
    alert = await _get_alert(session, alert_id)
    await _require(user, Permission.RESOLVE_ALERTS)
    if alert.status == AlertStatus.RESOLVED:
        raise ActionError("action_err.already_resolved")
    if alert.status != AlertStatus.ACKNOWLEDGED:
        raise ActionError("action_err.ack_first")
    return alert


async def apply_action(
    session: AsyncSession,
    alert_id: int,
    action: AlertActionType,
    user: User | None,
    *,
    reason: str | None = None,
) -> Text:
    alert = await _get_alert(session, alert_id)
    now = datetime.now(UTC)

    if action == AlertActionType.ACK:
        await _require(user, Permission.ACK_ALERTS)
        if alert.status != AlertStatus.NEW:
            raise ActionError("action_err.already_acked")
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_by_user = user
        alert.acknowledged_at = now
        reply = Text("action.acked")

    elif action == AlertActionType.ASSIGN:
        await _require(user, Permission.ACK_ALERTS)
        if alert.status == AlertStatus.RESOLVED:
            raise ActionError("action_err.closed")
        if alert.status == AlertStatus.NEW:
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_by_user = user
            alert.acknowledged_at = now
        alert.assigned_to_user = user
        alert.assigned_at = now
        reply = Text("action.assigned")

    elif action == AlertActionType.RESOLVE:
        await check_can_resolve(session, alert_id, user)
        alert.status = AlertStatus.RESOLVED
        alert.resolved_by_user = user
        alert.resolved_at = now
        alert.resolution_reason = reason if reason in RESOLUTION_REASONS else "other"
        reply = Text("action.resolved")

    elif action == AlertActionType.MUTE:
        await _require(user, Permission.ACK_ALERTS)
        if alert.vehicle_id is None:
            raise ActionError("action_err.no_vehicle")
        session.add(
            AlertMute(
                vehicle_id=alert.vehicle_id,
                alert_type=alert.alert_type,
                muted_until=now + MUTE_DURATION,
                muted_by=user.id,
            )
        )
        reply = Text("action.muted", type=Text(f"alert.type.{alert.alert_type}"))

    else:
        raise ActionError("action_err.unknown")

    details = {"reason": alert.resolution_reason} if action == AlertActionType.RESOLVE else None
    session.add(AuditLog(actor_id=user.id, action=f"alert_{action.value}", target=f"alert:{alert.id}", details=details))
    await session.commit()
    return reply


async def add_note(session: AsyncSession, alert_id: int, user: User | None, text: str) -> None:
    """Alert xabariga reply qilingan izoh — guruhdagi muhokama alert tarixida qoladi."""
    alert = await _get_alert(session, alert_id)
    await _require(user, Permission.ACK_ALERTS)
    line = f"{display_name(user)}: {' '.join(text.split())}"[:500]
    alert.resolution_note = f"{alert.resolution_note}\n{line}" if alert.resolution_note else line
    alert.resolution_note = alert.resolution_note[-MAX_NOTE_LENGTH:]
    session.add(AuditLog(actor_id=user.id, action="alert_note", target=f"alert:{alert.id}"))
    await session.commit()


async def find_alert_by_message(session: AsyncSession, chat_id: int, message_id: int) -> int | None:
    return await session.scalar(
        select(AlertDelivery.alert_id).where(AlertDelivery.chat_id == chat_id, AlertDelivery.message_id == message_id)
    )


async def _require(user: User | None, permission: Permission) -> None:
    if not await has_permission(user, permission):
        raise ActionError("access.no_permission")


async def refresh_alert_messages(bot: Bot, session: AsyncSession, alert_id: int) -> None:
    """Alert yuborilgan barcha chatlardagi xabarni yangi holat bilan, har birini o'z tilida tahrirlaydi."""
    alert = await session.scalar(
        select(Alert)
        .options(selectinload(Alert.deliveries))
        .where(Alert.id == alert_id)
        .execution_options(populate_existing=True)
    )
    if alert is None:
        return
    for delivery in alert.deliveries:
        if delivery.status != DeliveryStatus.SENT or delivery.message_id is None:
            continue
        text, markup = render_delivery(alert, delivery, await delivery_context(session, delivery))
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
