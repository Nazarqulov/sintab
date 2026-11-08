from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.db.models import Alert, User, UserStatus
from app.services.alerts.types import Severity
from app.services.preferences import in_quiet_hours, is_muted
from app.services.scope import can_see_alert
from app.services.users import has_permission

LEVEL_SEVERITIES = {
    "critical": {Severity.CRITICAL.value},
    "warning": {Severity.CRITICAL.value, Severity.WARNING.value},
    "all": {s.value for s in Severity},
}


async def personal_recipients(
    session: AsyncSession, alert: Alert, depot_id: int | None, now: datetime
) -> list[User]:
    """Shaxsiy bildirishnoma yoqqan, alertni ko'ra oladigan va hozir jim/mute bo'lmagan foydalanuvchilar."""
    candidates = await session.scalars(
        select(User).where(User.status == UserStatus.ACTIVE, User.notify_level != "off")
    )
    recipients = []
    for user in candidates:
        if alert.severity not in LEVEL_SEVERITIES.get(user.notify_level, set()):
            continue
        if is_muted(user, now):
            continue
        if alert.severity != Severity.CRITICAL.value and in_quiet_hours(user, now):
            continue
        if not await has_permission(user, Permission.VIEW_ALERTS):
            continue
        if not await can_see_alert(user, alert.driver_id, depot_id):
            continue
        recipients.append(user)
    return recipients
