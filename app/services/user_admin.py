"""Foydalanuvchilarni boshqarish. Har bir amal huquq va ierarxiya bilan tekshiriladi, audit log'ga yoziladi.

Ierarxiya: Owner'ga hech kim tega olmaydi; Admin'ni faqat Owner boshqaradi va tayinlaydi;
hech kim o'zini o'zgartira olmaydi.
"""

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.db.models import AuditLog, Depot, PermissionOverride, User, UserRole, UserStatus
from app.core.i18n import LocalizedError
from app.services.users import has_permission

ASSIGNABLE_BY_ADMIN = [UserRole.DISPATCHER, UserRole.MANAGER, UserRole.VIEWER, UserRole.DRIVER]
ASSIGNABLE_BY_OWNER = [UserRole.ADMIN, *ASSIGNABLE_BY_ADMIN]
# Bu rollar barcha depolarni ko'radi — depo biriktirish kerak emas.
GLOBAL_ROLES = {UserRole.OWNER, UserRole.ADMIN}
PAGE_SIZE = 8


class AdminError(LocalizedError):
    """Foydalanuvchiga ko'rsatiladigan rad javobi (tarjima kaliti bilan)."""


def assignable_roles(actor: User) -> list[UserRole]:
    return ASSIGNABLE_BY_OWNER if actor.role == UserRole.OWNER else ASSIGNABLE_BY_ADMIN


async def _check(actor: User | None, target: User, *permissions: Permission) -> User:
    if actor is None or actor.status != UserStatus.ACTIVE:
        raise AdminError("access.no_access")
    for permission in permissions:
        if not await has_permission(actor, permission):
            raise AdminError("access.no_permission")
    if target.id == actor.id:
        raise AdminError("admin_err.self")
    if target.role == UserRole.OWNER:
        raise AdminError("admin_err.owner")
    if target.role == UserRole.ADMIN and actor.role != UserRole.OWNER:
        raise AdminError("admin_err.admin_owner_only")
    # Keyingi sinxron kirishlar async'da lazy-load qilmasligi uchun kolleksiyalarni oldindan yuklaymiz.
    await target.awaitable_attrs.depots
    await target.awaitable_attrs.permission_overrides
    return actor


def _audit(session: AsyncSession, actor: User, action: str, target: User, **details) -> None:
    session.add(AuditLog(actor_id=actor.id, action=action, target=f"user:{target.id}", details=details or None))


async def set_role(session: AsyncSession, actor: User | None, target: User, role: UserRole) -> None:
    actor = await _check(actor, target, Permission.MANAGE_USERS, Permission.MANAGE_ROLES)
    if role not in assignable_roles(actor):
        raise AdminError("admin_err.role_not_assignable")
    previous = target.role
    target.role = role
    if role in GLOBAL_ROLES:
        target.depots = []
    if target.status == UserStatus.ACTIVE and previous != role:
        _audit(session, actor, "role_changed", target, before=previous.value, after=role.value)
    await session.commit()


async def toggle_depot(session: AsyncSession, actor: User | None, target: User, depot_id: int) -> None:
    actor = await _check(actor, target, Permission.MANAGE_USERS)
    depot = await session.get(Depot, depot_id)
    if depot is None:
        raise AdminError("depot_err.not_found")
    if depot in target.depots:
        target.depots.remove(depot)
    else:
        target.depots.append(depot)
    if target.status == UserStatus.ACTIVE:
        _audit(session, actor, "depots_changed", target, depots=[d.name for d in target.depots])
    await session.commit()


async def approve(session: AsyncSession, actor: User | None, target: User) -> None:
    actor = await _check(actor, target, Permission.MANAGE_USERS, Permission.MANAGE_ROLES)
    if target.status != UserStatus.PENDING:
        raise AdminError("admin_err.already_reviewed")
    if target.role not in assignable_roles(actor):
        raise AdminError("admin_err.choose_role_first")
    target.status = UserStatus.ACTIVE
    _audit(session, actor, "user_approved", target, role=target.role.value, depots=[d.name for d in target.depots])
    await session.commit()


async def reject(session: AsyncSession, actor: User | None, target: User) -> None:
    """Rad etilgan foydalanuvchi bloklanadi — qayta-qayta so'rov yuborib spam qila olmasin."""
    actor = await _check(actor, target, Permission.MANAGE_USERS)
    if target.status != UserStatus.PENDING:
        raise AdminError("admin_err.already_reviewed")
    target.status = UserStatus.BLOCKED
    _audit(session, actor, "user_rejected", target)
    await session.commit()


async def set_blocked(session: AsyncSession, actor: User | None, target: User, blocked: bool) -> None:
    actor = await _check(actor, target, Permission.MANAGE_USERS)
    if blocked:
        target.status = UserStatus.BLOCKED
    else:
        # Rad etilgan (hali rol olmagan) foydalanuvchi blokdan chiqsa — qayta ko'rib chiqiladi.
        target.status = UserStatus.PENDING if target.role == UserRole.PENDING else UserStatus.ACTIVE
    _audit(session, actor, "user_blocked" if blocked else "user_unblocked", target)
    await session.commit()


async def delete_user(session: AsyncSession, actor: User | None, target: User) -> None:
    actor = await _check(actor, target, Permission.MANAGE_USERS)
    _audit(
        session, actor, "user_deleted", target,
        telegram_id=target.telegram_id, full_name=target.full_name, role=target.role.value,
    )
    await session.delete(target)
    await session.commit()


async def cycle_override(session: AsyncSession, actor: User | None, target: User, permission: Permission) -> None:
    """Rol standarti → majburan berish → olib qo'yish → standart. Faqat Owner."""
    if actor is None or actor.role != UserRole.OWNER:
        raise AdminError("admin_err.overrides_owner_only")
    actor = await _check(actor, target)
    override = next((o for o in target.permission_overrides if o.permission == permission.value), None)
    if override is None:
        target.permission_overrides.append(
            PermissionOverride(permission=permission.value, allowed=True, granted_by=actor.id)
        )
        state = "granted"
    elif override.allowed:
        override.allowed = False
        override.granted_by = actor.id
        state = "revoked"
    else:
        target.permission_overrides.remove(override)
        state = "default"
    _audit(session, actor, "permission_override", target, permission=permission.value, state=state)
    await session.commit()


async def list_users(session: AsyncSession, page: int) -> tuple[list[User], int]:
    """Kutayotganlar birinchi, keyin faollar, keyin bloklanganlar."""
    order = case(
        (User.status == UserStatus.PENDING, 0),
        (User.status == UserStatus.ACTIVE, 1),
        else_=2,
    )
    users = (await session.scalars(select(User).order_by(order, User.full_name, User.id))).all()
    pages = max((len(users) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 0), pages - 1)
    return list(users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]), pages
