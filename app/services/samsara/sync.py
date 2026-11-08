from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Driver, Vehicle
from app.services.depots import apply_tag_mapping
from app.services.samsara.client import SamsaraClient


async def sync_fleet(session: AsyncSession, client: SamsaraClient) -> tuple[int, int]:
    """Samsara'dagi mashina va haydovchilarni bazaga yozadi. depot_id'ga tegmaydi — uni admin belgilaydi."""
    vehicles = await client.list_vehicles()
    drivers = await client.list_drivers()

    existing_vehicles = {v.samsara_id: v for v in (await session.scalars(select(Vehicle))).all()}
    for item in vehicles:
        samsara_id = str(item.get("id") or "")
        if not samsara_id:
            continue
        vehicle = existing_vehicles.get(samsara_id) or Vehicle(samsara_id=samsara_id)
        vehicle.name = item.get("name") or vehicle.name
        vehicle.plate = item.get("licensePlate") or vehicle.plate
        vehicle.tag = _first_tag(item) or vehicle.tag
        await apply_tag_mapping(session, vehicle)
        session.add(vehicle)

    existing_drivers = {d.samsara_id: d for d in (await session.scalars(select(Driver))).all()}
    for item in drivers:
        samsara_id = str(item.get("id") or "")
        if not samsara_id:
            continue
        driver = existing_drivers.get(samsara_id) or Driver(samsara_id=samsara_id)
        driver.full_name = item.get("name") or driver.full_name
        driver.phone = item.get("phone") or driver.phone
        session.add(driver)

    await session.commit()
    return len(vehicles), len(drivers)


def _first_tag(item: dict[str, Any]) -> str | None:
    tags = item.get("tags") or []
    if tags and isinstance(tags[0], dict):
        return tags[0].get("name")
    return None
