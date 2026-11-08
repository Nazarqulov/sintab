from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Group, RoutingRule


async def find_target_groups(
    session: AsyncSession,
    *,
    alert_type: str,
    severity: str,
    depot_id: int | None,
    vehicle_tag: str | None,
) -> list[Group]:
    """Mos keladigan BARCHA qoidalarning guruhlari (masalan depo guruhi + markaziy Critical guruh).

    Qoidadagi NULL maydon — "har qanday". Tartib: priority kamayish bo'yicha, takrorsiz.
    """
    stmt = (
        select(RoutingRule)
        .options(selectinload(RoutingRule.group))
        .where(
            or_(RoutingRule.alert_type.is_(None), RoutingRule.alert_type == alert_type),
            or_(RoutingRule.severity.is_(None), RoutingRule.severity == severity),
            or_(RoutingRule.depot_id.is_(None), RoutingRule.depot_id == depot_id),
            or_(RoutingRule.vehicle_tag.is_(None), RoutingRule.vehicle_tag == vehicle_tag),
        )
        .order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    groups: list[Group] = []
    seen: set[int] = set()
    for rule in (await session.scalars(stmt)).all():
        if rule.group_id not in seen:
            seen.add(rule.group_id)
            groups.append(rule.group)
    return groups
