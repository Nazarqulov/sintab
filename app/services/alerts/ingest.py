import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alert, AlertDelivery, AlertMute, AppSetting, Driver, Vehicle
from app.services.alerts.types import SEVERITY_OVERRIDES_SETTING, resolve_severity
from app.services.routing.service import find_target_groups
from app.services.samsara.parser import NormalizedAlert

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    alert_id: int | None
    created: bool
    delivery_ids: list[int] = field(default_factory=list)
    suppressed: bool = False


async def ingest_alert(session: AsyncSession, data: NormalizedAlert, *, owner_chat_id: int) -> IngestResult:
    existing_id = await _find_alert_id(session, data.event_id)
    if existing_id is not None:
        return IngestResult(alert_id=existing_id, created=False)

    vehicle = await _upsert_vehicle(session, data)
    driver = await _upsert_driver(session, data)
    severity = resolve_severity(data.alert_type, data.resolved, await _severity_overrides(session))
    message = f"✅ Samsara: bartaraf etildi — {data.description}" if data.resolved else data.description

    alert = Alert(
        samsara_alert_id=data.event_id,
        alert_type=data.alert_type,
        severity=severity.value,
        vehicle=vehicle,
        driver=driver,
        message=message,
        payload=data.raw,
        happened_at=data.happened_at,
        latitude=data.latitude,
        longitude=data.longitude,
        address=data.address,
        samsara_url=data.url,
    )
    session.add(alert)
    try:
        await session.flush()
    except IntegrityError:
        # Bir xil eventId parallel kelgan — ikkinchisi takror hisoblanadi.
        await session.rollback()
        return IngestResult(alert_id=await _find_alert_id(session, data.event_id), created=False)

    if vehicle is not None and await _is_muted(session, vehicle.id, data.alert_type):
        alert.suppressed = True
        await session.commit()
        return IngestResult(alert_id=alert.id, created=True, suppressed=True)

    groups = await find_target_groups(
        session,
        alert_type=alert.alert_type,
        severity=alert.severity,
        depot_id=vehicle.depot_id if vehicle else None,
        vehicle_tag=vehicle.tag if vehicle else None,
    )
    if groups:
        deliveries = [
            AlertDelivery(alert_id=alert.id, group_id=g.id, chat_id=g.chat_id, topic_id=g.topic_id) for g in groups
        ]
    else:
        # Alert jimgina yo'qolib ketmasligi uchun: qoida yo'q bo'lsa Owner'ga.
        logger.warning("Alert %s uchun routing qoidasi topilmadi — Owner'ga yuboriladi", alert.id)
        deliveries = [AlertDelivery(alert_id=alert.id, chat_id=owner_chat_id)]
    session.add_all(deliveries)
    await session.commit()
    return IngestResult(alert_id=alert.id, created=True, delivery_ids=[d.id for d in deliveries])


async def _find_alert_id(session: AsyncSession, event_id: str) -> int | None:
    return await session.scalar(select(Alert.id).where(Alert.samsara_alert_id == event_id))


async def _upsert_vehicle(session: AsyncSession, data: NormalizedAlert) -> Vehicle | None:
    if not data.vehicle_samsara_id:
        return None
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.samsara_id == data.vehicle_samsara_id))
    if vehicle is None:
        vehicle = Vehicle(samsara_id=data.vehicle_samsara_id)
        session.add(vehicle)
    if data.vehicle_name:
        vehicle.name = data.vehicle_name
    if data.latitude is not None and data.longitude is not None:
        vehicle.last_location = {"latitude": data.latitude, "longitude": data.longitude, "address": data.address}
        vehicle.last_seen_at = data.happened_at
    await session.flush()
    return vehicle


async def _upsert_driver(session: AsyncSession, data: NormalizedAlert) -> Driver | None:
    if not data.driver_samsara_id:
        return None
    driver = await session.scalar(select(Driver).where(Driver.samsara_id == data.driver_samsara_id))
    if driver is None:
        driver = Driver(samsara_id=data.driver_samsara_id)
        session.add(driver)
    if data.driver_name:
        driver.full_name = data.driver_name
    await session.flush()
    return driver


async def _severity_overrides(session: AsyncSession) -> dict[str, str]:
    setting = await session.get(AppSetting, SEVERITY_OVERRIDES_SETTING)
    return setting.value if setting and isinstance(setting.value, dict) else {}


async def _is_muted(session: AsyncSession, vehicle_id: int, alert_type: str) -> bool:
    stmt = select(
        exists().where(
            AlertMute.vehicle_id == vehicle_id,
            AlertMute.alert_type == alert_type,
            AlertMute.muted_until > datetime.now(UTC),
        )
    )
    return bool(await session.scalar(stmt))
