"""Kim nimani ko'radi: Owner/Admin — hammasi; depo biriktirilganlar — o'z depolari; Driver — faqat o'zi.

Depo biriktirilmagan (Owner/Admin bo'lmagan) xodim butun parkni ko'radi — kichik kompaniyalar depo ishlatmasligi mumkin.
"""

from sqlalchemy import Select, false, or_, select

from app.db.models import Alert, Driver, User, UserRole, Vehicle

GLOBAL_ROLES = {UserRole.OWNER, UserRole.ADMIN}


async def depot_scope(user: User) -> set[int] | None:
    """None — cheklovsiz."""
    if user.role in GLOBAL_ROLES:
        return None
    depots = await user.awaitable_attrs.depots
    return {d.id for d in depots} or None


async def can_see_alert(user: User, driver_id: int | None, depot_id: int | None) -> bool:
    if user.role == UserRole.DRIVER:
        return user.driver_id is not None and driver_id == user.driver_id
    scope = await depot_scope(user)
    return scope is None or depot_id in scope


async def scoped_alerts(user: User) -> Select:
    stmt = select(Alert)
    if user.role == UserRole.DRIVER:
        return stmt.where(Alert.driver_id == user.driver_id) if user.driver_id else stmt.where(false())
    scope = await depot_scope(user)
    if scope is not None:
        stmt = stmt.join(Vehicle, Alert.vehicle_id == Vehicle.id).where(Vehicle.depot_id.in_(scope))
    return stmt


async def scoped_vehicles(user: User) -> Select:
    stmt = select(Vehicle)
    if user.role == UserRole.DRIVER:
        if not user.driver_id:
            return stmt.where(false())
        # Haydovchining alertlarida qatnashgan mashinalar (Samsara biriktirish ma'lumoti token bilan aniqlanadi).
        own = select(Alert.vehicle_id).where(Alert.driver_id == user.driver_id, Alert.vehicle_id.is_not(None))
        return stmt.where(Vehicle.id.in_(own))
    scope = await depot_scope(user)
    return stmt.where(Vehicle.depot_id.in_(scope)) if scope is not None else stmt


async def scoped_drivers(user: User) -> Select:
    stmt = select(Driver)
    if user.role == UserRole.DRIVER:
        return stmt.where(Driver.id == user.driver_id) if user.driver_id else stmt.where(false())
    scope = await depot_scope(user)
    if scope is not None:
        stmt = stmt.where(or_(Driver.depot_id.in_(scope), Driver.depot_id.is_(None)))
    return stmt
