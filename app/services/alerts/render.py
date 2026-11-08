from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.alerts import alert_keyboard
from app.core.config import settings
from app.db.models import Alert, AlertDelivery, DeliveryKind, Group
from app.services.alerts.formatter import as_utc, format_alert
from app.services.users import get_user_by_telegram_id


@dataclass
class RenderContext:
    lang: str
    tz: str


async def delivery_context(session: AsyncSession, delivery: AlertDelivery) -> RenderContext:
    """Guruh — o'z tili va kompaniya vaqt zonasi; shaxsiy chat — foydalanuvchining tili va zonasi."""
    if delivery.group_id is not None:
        group = await session.get(Group, delivery.group_id)
        if group is not None:
            return RenderContext(group.language, settings.default_timezone)
    if delivery.chat_id > 0:
        user = await get_user_by_telegram_id(session, delivery.chat_id)
        if user is not None:
            return RenderContext(user.language, user.timezone)
    return RenderContext(settings.default_language, settings.default_timezone)


def escalation_minutes(alert: Alert) -> int | None:
    if alert.escalated_at is None:
        return None
    return max(round((as_utc(alert.escalated_at) - as_utc(alert.created_at)).total_seconds() / 60), 1)


def render_delivery(
    alert: Alert, delivery: AlertDelivery, context: RenderContext
) -> tuple[str, InlineKeyboardMarkup | None]:
    minutes = escalation_minutes(alert) if delivery.kind == DeliveryKind.ESCALATION.value else None
    text = format_alert(alert, context.tz, context.lang, escalation_minutes=minutes)
    return text, alert_keyboard(alert, context.lang)
