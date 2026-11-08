from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Alert, AlertDelivery, AlertMute, AppSetting, Depot, Driver, Vehicle
from app.services.alerts.ingest import ingest_alert
from tests.helpers import OWNER_CHAT_ID, add_group, add_rule, add_vehicle, normalized


async def test_alert_without_rules_goes_to_owner(session):
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)

    assert result.created
    deliveries = (await session.scalars(select(AlertDelivery))).all()
    assert [(d.chat_id, d.group_id) for d in deliveries] == [(OWNER_CHAT_ID, None)]
    alert = await session.get(Alert, result.alert_id)
    assert alert.severity == "critical"
    assert alert.vehicle.name == "Truck 1"
    assert alert.driver.full_name == "Aziz"
    assert (alert.latitude, alert.address, alert.samsara_url) == (41.3, "Tashkent", "https://cloud.samsara.com/x")


async def test_duplicate_event_is_not_delivered_twice(session):
    first = await ingest_alert(session, normalized("same"), owner_chat_id=OWNER_CHAT_ID)
    second = await ingest_alert(session, normalized("same"), owner_chat_id=OWNER_CHAT_ID)

    assert first.created and not second.created
    assert second.alert_id == first.alert_id
    assert second.delivery_ids == []
    assert len((await session.scalars(select(AlertDelivery))).all()) == 1


async def test_routing_by_type_severity_depot_and_tag(session):
    north, south = Depot(name="North"), Depot(name="South")
    session.add_all([north, south])
    await session.commit()
    await add_vehicle(session, "veh-1", depot=north, tag="reefer")

    north_group = await add_group(session, -101, depot=north)
    south_group = await add_group(session, -102, depot=south)
    critical_group = await add_group(session, -103, topic_id=7)
    reefer_group = await add_group(session, -104)
    speeding_group = await add_group(session, -105)

    await add_rule(session, north_group, depot_id=north.id)
    await add_rule(session, south_group, depot_id=south.id)
    await add_rule(session, critical_group, severity="critical", priority=10)
    await add_rule(session, reefer_group, vehicle_tag="reefer")
    await add_rule(session, speeding_group, alert_type="speeding")

    result = await ingest_alert(session, normalized(alert_type="crash"), owner_chat_id=OWNER_CHAT_ID)
    deliveries = (
        await session.scalars(select(AlertDelivery).where(AlertDelivery.alert_id == result.alert_id))
    ).all()

    # Critical (priority 10) birinchi; South va speeding qoidalari mos kelmaydi.
    assert [d.chat_id for d in deliveries] == [-103, -101, -104]
    assert deliveries[0].topic_id == 7


async def test_same_group_matched_by_two_rules_gets_one_delivery(session):
    group = await add_group(session, -200)
    await add_rule(session, group, severity="critical")
    await add_rule(session, group, alert_type="crash")

    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    assert len(result.delivery_ids) == 1


async def test_active_mute_suppresses_delivery(session):
    vehicle = await add_vehicle(session, "veh-1")
    session.add(AlertMute(vehicle_id=vehicle.id, alert_type="crash", muted_until=datetime.now(UTC) + timedelta(hours=1)))
    await session.commit()

    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)

    assert result.created and result.suppressed and result.delivery_ids == []
    assert (await session.get(Alert, result.alert_id)).suppressed is True


async def test_expired_mute_and_other_type_do_not_suppress(session):
    vehicle = await add_vehicle(session, "veh-1")
    session.add_all([
        AlertMute(vehicle_id=vehicle.id, alert_type="crash", muted_until=datetime.now(UTC) - timedelta(minutes=1)),
        AlertMute(vehicle_id=vehicle.id, alert_type="speeding", muted_until=datetime.now(UTC) + timedelta(hours=1)),
    ])
    await session.commit()

    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    assert not result.suppressed and len(result.delivery_ids) == 1


async def test_severity_override_from_settings(session):
    session.add(AppSetting(key="severity_overrides", value={"crash": "warning"}))
    await session.commit()

    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    assert (await session.get(Alert, result.alert_id)).severity == "warning"


async def test_resolved_event_is_info_and_marked(session):
    result = await ingest_alert(session, normalized(resolved=True), owner_chat_id=OWNER_CHAT_ID)
    alert = await session.get(Alert, result.alert_id)
    assert alert.severity == "info"
    assert alert.samsara_resolved is True and alert.message == "Crash detected"


async def test_existing_vehicle_and_driver_are_reused_and_renamed(session):
    await ingest_alert(session, normalized("a"), owner_chat_id=OWNER_CHAT_ID)
    await ingest_alert(session, normalized("b", vehicle_name="Truck 1 (new)"), owner_chat_id=OWNER_CHAT_ID)

    vehicles = (await session.scalars(select(Vehicle))).all()
    assert [v.name for v in vehicles] == ["Truck 1 (new)"]
    assert len((await session.scalars(select(Driver))).all()) == 1
