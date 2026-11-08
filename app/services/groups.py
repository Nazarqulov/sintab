from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import LANGUAGES, LocalizedError
from app.core.permissions import Permission
from app.db.models import AuditLog, Depot, Group, RoutingRule, User
from app.services.alerts.types import AlertType, Severity
from app.services.users import has_permission

PAGE_SIZE = 8


class GroupError(LocalizedError):
    pass


async def _require(user: User, permission: Permission) -> None:
    if not await has_permission(user, permission):
        raise GroupError("access.no_permission")


def _audit(session: AsyncSession, actor: User, action: str, group_id: int, **details) -> None:
    session.add(AuditLog(actor_id=actor.id, action=action, target=f"group:{group_id}", details=details or None))


async def bind_group(
    session: AsyncSession, actor: User, chat_id: int, topic_id: int | None, title: str | None, language: str
) -> tuple[Group, bool]:
    """(guruh, yangi_yaratildimi)."""
    await _require(actor, Permission.MANAGE_GROUPS)
    existing = await session.scalar(
        select(Group).where(
            Group.chat_id == chat_id, Group.topic_id.is_(None) if topic_id is None else Group.topic_id == topic_id
        )
    )
    if existing is not None:
        existing.title = title or existing.title
        await session.commit()
        return existing, False
    group = Group(chat_id=chat_id, topic_id=topic_id, title=title, bound_by=actor.id, language=language)
    session.add(group)
    await session.flush()
    _audit(session, actor, "group_bound", group.id, chat_id=chat_id, topic_id=topic_id, title=title)
    await session.commit()
    return group, True


async def unbind_group(session: AsyncSession, actor: User, group_id: int) -> str:
    await _require(actor, Permission.MANAGE_GROUPS)
    group = await get_group(session, group_id)
    title = group.title or str(group.chat_id)
    _audit(session, actor, "group_unbound", group.id, chat_id=group.chat_id, title=group.title)
    await session.delete(group)
    await session.commit()
    return title


async def find_bound_group(session: AsyncSession, chat_id: int, topic_id: int | None) -> Group | None:
    return await session.scalar(
        select(Group).where(
            Group.chat_id == chat_id, Group.topic_id.is_(None) if topic_id is None else Group.topic_id == topic_id
        )
    )


async def get_group(session: AsyncSession, group_id: int) -> Group:
    group = await session.get(Group, group_id)
    if group is None:
        raise GroupError("groups.not_found")
    return group


async def list_groups(session: AsyncSession, page: int) -> tuple[list[Group], int]:
    total = await session.scalar(select(func.count(Group.id))) or 0
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 0), pages - 1)
    groups = await session.scalars(select(Group).order_by(Group.title, Group.id).offset(page * PAGE_SIZE).limit(PAGE_SIZE))
    return list(groups), pages


async def group_rules(session: AsyncSession, group_id: int) -> list[RoutingRule]:
    rules = await session.scalars(
        select(RoutingRule).where(RoutingRule.group_id == group_id).order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    return list(rules)


async def set_group_depot(session: AsyncSession, actor: User, group_id: int, depot_id: int | None) -> None:
    await _require(actor, Permission.MANAGE_GROUPS)
    group = await get_group(session, group_id)
    if depot_id is not None and await session.get(Depot, depot_id) is None:
        raise GroupError("depot_err.not_found")
    group.depot_id = depot_id
    _audit(session, actor, "group_depot", group.id, depot_id=depot_id)
    await session.commit()


async def set_group_language(session: AsyncSession, actor: User, group_id: int, language: str) -> None:
    await _require(actor, Permission.MANAGE_GROUPS)
    if language not in LANGUAGES:
        raise GroupError("settings.invalid")
    group = await get_group(session, group_id)
    group.language = language
    _audit(session, actor, "group_language", group.id, language=language)
    await session.commit()


async def toggle_escalation(session: AsyncSession, actor: User, group_id: int) -> bool:
    await _require(actor, Permission.MANAGE_GROUPS)
    group = await get_group(session, group_id)
    group.is_escalation = not group.is_escalation
    _audit(session, actor, "group_escalation", group.id, enabled=group.is_escalation)
    await session.commit()
    return group.is_escalation


async def add_rule(
    session: AsyncSession,
    actor: User,
    group_id: int,
    *,
    alert_type: str | None,
    severity: str | None,
    depot_id: int | None,
    vehicle_tag: str | None,
) -> RoutingRule:
    await _require(actor, Permission.MANAGE_ROUTING)
    await get_group(session, group_id)
    if alert_type is not None and alert_type not in {a.value for a in AlertType}:
        raise GroupError("settings.invalid")
    if severity is not None and severity not in {s.value for s in Severity}:
        raise GroupError("settings.invalid")
    if depot_id is not None and await session.get(Depot, depot_id) is None:
        raise GroupError("depot_err.not_found")
    # Aniqroq qoida birinchi ko'rsatilsin: har bir to'ldirilgan shart +1.
    priority = sum(value is not None for value in (alert_type, severity, depot_id, vehicle_tag))
    rule = RoutingRule(
        group_id=group_id,
        alert_type=alert_type,
        severity=severity,
        depot_id=depot_id,
        vehicle_tag=vehicle_tag,
        priority=priority,
    )
    session.add(rule)
    await session.flush()
    _audit(
        session, actor, "rule_added", group_id,
        rule_id=rule.id, alert_type=alert_type, severity=severity, depot_id=depot_id, vehicle_tag=vehicle_tag,
    )
    await session.commit()
    return rule


async def delete_rule(session: AsyncSession, actor: User, rule_id: int) -> None:
    await _require(actor, Permission.MANAGE_ROUTING)
    rule = await session.get(RoutingRule, rule_id)
    if rule is None:
        raise GroupError("groups.rule_not_found")
    _audit(session, actor, "rule_deleted", rule.group_id, rule_id=rule_id)
    await session.delete(rule)
    await session.commit()
