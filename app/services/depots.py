from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Depot, User


class DepotError(Exception):
    pass


async def list_depots(session: AsyncSession) -> list[Depot]:
    return list((await session.scalars(select(Depot).order_by(Depot.name))).all())


async def create_depot(session: AsyncSession, actor: User, name: str) -> Depot:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 128:
        raise DepotError("Nom 2–128 belgi bo'lishi kerak.")
    exists = await session.scalar(select(Depot.id).where(func.lower(Depot.name) == name.lower()))
    if exists:
        raise DepotError("Bunday depo allaqachon bor.")
    depot = Depot(name=name)
    session.add(depot)
    await session.flush()
    session.add(AuditLog(actor_id=actor.id, action="depot_created", target=f"depot:{depot.id}", details={"name": name}))
    await session.commit()
    return depot


async def delete_depot(session: AsyncSession, actor: User, depot_id: int) -> str:
    """Depo o'chsa: foydalanuvchi bog'lanishlari o'chadi, mashina/guruh/qoidalarda depot_id NULL bo'ladi."""
    depot = await session.get(Depot, depot_id)
    if depot is None:
        raise DepotError("Depo topilmadi.")
    name = depot.name
    session.add(AuditLog(actor_id=actor.id, action="depot_deleted", target=f"depot:{depot_id}", details={"name": name}))
    await session.delete(depot)
    await session.commit()
    return name
