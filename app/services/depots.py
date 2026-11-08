from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import LocalizedError
from app.db.models import AppSetting, AuditLog, Depot, User, Vehicle

TAG_DEPOT_SETTING = "tag_depot_map"


class DepotError(LocalizedError):
    pass


async def list_depots(session: AsyncSession) -> list[Depot]:
    return list((await session.scalars(select(Depot).order_by(Depot.name))).all())


async def create_depot(session: AsyncSession, actor: User, name: str) -> Depot:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 128:
        raise DepotError("depot_err.name_length")
    exists = await session.scalar(select(Depot.id).where(func.lower(Depot.name) == name.lower()))
    if exists:
        raise DepotError("depot_err.exists")
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
        raise DepotError("depot_err.not_found")
    name = depot.name
    mapping = await tag_depot_map(session)
    remaining = {tag: did for tag, did in mapping.items() if did != depot_id}
    if remaining != mapping:
        await _save_map(session, remaining)
    session.add(AuditLog(actor_id=actor.id, action="depot_deleted", target=f"depot:{depot_id}", details={"name": name}))
    await session.delete(depot)
    await session.commit()
    return name


async def distinct_tags(session: AsyncSession) -> list[str]:
    rows = await session.scalars(select(Vehicle.tag).where(Vehicle.tag.is_not(None)).distinct().order_by(Vehicle.tag))
    return [tag for tag in rows if tag]


async def tag_depot_map(session: AsyncSession) -> dict[str, int]:
    setting = await session.get(AppSetting, TAG_DEPOT_SETTING)
    return dict(setting.value) if setting and isinstance(setting.value, dict) else {}


async def _save_map(session: AsyncSession, mapping: dict[str, int]) -> None:
    setting = await session.get(AppSetting, TAG_DEPOT_SETTING)
    if setting is None:
        session.add(AppSetting(key=TAG_DEPOT_SETTING, value=mapping))
    else:
        setting.value = mapping


async def map_tag_to_depot(session: AsyncSession, actor: User, tag: str, depot_id: int) -> int:
    """Samsara tag'idagi barcha mashinalarni depoga biriktiradi; keyin keladigan mashinalar ham shu qoidaga bo'ysunadi."""
    depot = await session.get(Depot, depot_id)
    if depot is None:
        raise DepotError("depot_err.not_found")
    mapping = await tag_depot_map(session)
    mapping[tag] = depot_id
    await _save_map(session, mapping)
    result = await session.execute(update(Vehicle).where(Vehicle.tag == tag).values(depot_id=depot_id))
    session.add(
        AuditLog(
            actor_id=actor.id,
            action="tag_mapped",
            target=f"depot:{depot_id}",
            details={"tag": tag, "vehicles": result.rowcount},
        )
    )
    await session.commit()
    return result.rowcount


async def apply_tag_mapping(session: AsyncSession, vehicle: Vehicle) -> None:
    """Depo biriktirilmagan mashinaga tag bo'yicha depo beradi (ingest/sinxron paytida)."""
    if vehicle.depot_id is not None or not vehicle.tag:
        return
    depot_id = (await tag_depot_map(session)).get(vehicle.tag)
    if depot_id is not None:
        vehicle.depot_id = depot_id


async def set_vehicle_depot(session: AsyncSession, actor: User, vehicle_id: int, depot_id: int | None) -> Vehicle:
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise DepotError("vehicle_err.not_found")
    if depot_id is not None and await session.get(Depot, depot_id) is None:
        raise DepotError("depot_err.not_found")
    vehicle.depot_id = depot_id
    session.add(
        AuditLog(actor_id=actor.id, action="vehicle_depot", target=f"vehicle:{vehicle_id}", details={"depot_id": depot_id})
    )
    await session.commit()
    return vehicle
