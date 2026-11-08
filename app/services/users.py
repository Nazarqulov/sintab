from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission, effective_permissions
from app.db.models import AuditLog, User, UserRole, UserStatus


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def user_permissions(user: User | None) -> set[Permission]:
    if user is None or user.status != UserStatus.ACTIVE:
        return set()
    overrides = {o.permission: o.allowed for o in await user.awaitable_attrs.permission_overrides}
    return effective_permissions(user.role.value, overrides)


async def has_permission(user: User | None, permission: Permission) -> bool:
    return permission in await user_permissions(user)


def display_name(user: User | None) -> str:
    if user is None:
        return "—"
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return str(user.telegram_id)


async def ensure_owner(session: AsyncSession, telegram_id: int) -> User:
    """.env'dagi OWNER_TELEGRAM_ID har doim faol Owner bo'ladi."""
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id, role=UserRole.OWNER, status=UserStatus.ACTIVE)
        session.add(user)
        await session.flush()
        session.add(AuditLog(actor_id=user.id, action="owner_bootstrap", target=f"user:{user.id}"))
    else:
        user.role = UserRole.OWNER
        user.status = UserStatus.ACTIVE
    await session.commit()
    return user
