from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import (
    Alert,
    AlertDelivery,
    AlertStatus,
    DeliveryKind,
    DeliveryStatus,
    Depot,
    Driver,
    Group,
    UserRole,
    UserStatus,
)
from app.services.alerts.ingest import ingest_alert
from app.services.alerts.jobs import run_digest, run_escalations
from app.services.alerts.render import delivery_context, render_delivery
from app.services.app_settings import set_setting
from tests.helpers import OWNER_CHAT_ID, FakeBot, add_group, add_rule, add_user, add_vehicle, normalized

# 2026-01-15 — CST (UTC-6). Chicago 12:00 = 18:00 UTC, Chicago 23:00 = 05:00 UTC (16-yanvar).
NOON = datetime(2026, 1, 15, 18, 0, tzinfo=UTC)
NIGHT = datetime(2026, 1, 16, 5, 0, tzinfo=UTC)
MORNING = datetime(2026, 1, 16, 14, 0, tzinfo=UTC)


async def _deliveries(session, alert_id: int) -> list[AlertDelivery]:
    session.expire_all()
    return list((await session.scalars(select(AlertDelivery).where(AlertDelivery.alert_id == alert_id))).all())


async def _personal_chats(session, alert_id: int) -> set[int]:
    return {d.chat_id for d in await _deliveries(session, alert_id) if d.kind == DeliveryKind.PERSONAL.value}


# --- Shaxsiy bildirishnomalar ---

async def test_personal_notification_respects_level(session):
    await add_user(session, 11, UserRole.DISPATCHER, notify_level="critical")
    await add_user(session, 12, UserRole.DISPATCHER, notify_level="all")
    await add_user(session, 13, UserRole.DISPATCHER)  # standart: off

    crash = await ingest_alert(session, normalized("c", "crash"), owner_chat_id=OWNER_CHAT_ID, now=NOON)
    idle = await ingest_alert(session, normalized("i", "idle"), owner_chat_id=OWNER_CHAT_ID, now=NOON)

    assert await _personal_chats(session, crash.alert_id) == {11, 12}
    assert await _personal_chats(session, idle.alert_id) == {12}


async def test_muted_and_quiet_hours_users(session):
    await add_user(session, 11, UserRole.DISPATCHER, notify_level="all", muted_until=NIGHT + timedelta(hours=1))
    await add_user(session, 12, UserRole.DISPATCHER, notify_level="all", quiet_start=22, quiet_end=7)

    speeding = await ingest_alert(session, normalized("s", "speeding"), owner_chat_id=OWNER_CHAT_ID, now=NIGHT)
    crash = await ingest_alert(session, normalized("c", "crash"), owner_chat_id=OWNER_CHAT_ID, now=NIGHT)

    assert await _personal_chats(session, speeding.alert_id) == set()
    assert await _personal_chats(session, crash.alert_id) == {12}  # critical jim soatda ham keladi


async def test_depot_and_driver_scope_for_personal_alerts(session):
    north, south = Depot(name="North"), Depot(name="South")
    driver = Driver(samsara_id="drv-1", full_name="Aziz")
    session.add_all([north, south, driver])
    await session.commit()
    await add_vehicle(session, "veh-1", depot=north)

    north_user = await add_user(session, 11, UserRole.DISPATCHER, notify_level="all")
    south_user = await add_user(session, 12, UserRole.DISPATCHER, notify_level="all")
    (await north_user.awaitable_attrs.depots).append(north)
    (await south_user.awaitable_attrs.depots).append(south)
    await add_user(session, 13, UserRole.DRIVER, notify_level="all", driver_id=driver.id)
    await add_user(session, 14, UserRole.DRIVER, notify_level="all")
    await add_user(session, 15, UserRole.VIEWER, UserStatus.BLOCKED, notify_level="all")
    await session.commit()

    result = await ingest_alert(session, normalized("x", "crash"), owner_chat_id=OWNER_CHAT_ID, now=NOON)

    assert await _personal_chats(session, result.alert_id) == {11, 13}


async def test_owner_with_personal_notifications_is_not_duplicated(session):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER, notify_level="all")
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID, now=NOON)
    deliveries = await _deliveries(session, result.alert_id)
    assert [(d.chat_id, d.kind) for d in deliveries] == [(OWNER_CHAT_ID, "owner")]


async def test_personal_message_uses_user_language_and_timezone(session):
    await add_user(session, 11, UserRole.DISPATCHER, notify_level="all", language="ru", timezone="America/New_York")
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID, now=NOON)
    delivery = next(d for d in await _deliveries(session, result.alert_id) if d.chat_id == 11)
    alert = await session.get(Alert, result.alert_id)

    text, markup = render_delivery(alert, delivery, await delivery_context(session, delivery))

    assert "Машина" in text and "America/New_York" in text
    assert markup.inline_keyboard[0][0].text == "✅ Принял"


# --- Tungi digest ---

async def test_info_alert_at_night_is_deferred_and_digested_in_morning(session):
    group = await add_group(session, -100)
    await add_rule(session, group)
    idle = await ingest_alert(session, normalized("i1", "idle"), owner_chat_id=OWNER_CHAT_ID, now=NIGHT)
    idle2 = await ingest_alert(session, normalized("i2", "idle"), owner_chat_id=OWNER_CHAT_ID, now=NIGHT)
    crash = await ingest_alert(session, normalized("c", "crash"), owner_chat_id=OWNER_CHAT_ID, now=NIGHT)

    assert idle.delivery_ids == [] and idle2.delivery_ids == []
    assert len(crash.delivery_ids) == 1  # critical tunda ham darhol
    bot = FakeBot()

    assert await run_digest(session, bot, NIGHT) == 0  # hali tun
    assert await run_digest(session, bot, MORNING) == 1

    assert len(bot.sent) == 1
    assert "Tungi xulosa" in bot.sent[0]["text"] and "2" in bot.sent[0]["text"]
    statuses = {d.status for d in await _deliveries(session, idle.alert_id)}
    assert statuses == {DeliveryStatus.DIGESTED}
    assert await run_digest(session, bot, MORNING) == 0


async def test_digest_disabled_sends_info_immediately(session):
    await set_setting(session, None, "digest_enabled", False)
    result = await ingest_alert(session, normalized("i", "idle"), owner_chat_id=OWNER_CHAT_ID, now=NIGHT)
    assert len(result.delivery_ids) == 1


# --- Eskalatsiya ---

async def _age(session, alert_id: int, minutes: int, now: datetime) -> None:
    alert = await session.get(Alert, alert_id)
    alert.created_at = now - timedelta(minutes=minutes)
    await session.commit()


async def test_unacknowledged_critical_is_escalated_once(session):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await add_user(session, 2, UserRole.ADMIN)
    await add_user(session, 3, UserRole.DISPATCHER)
    escalation_group = await add_group(session, -900)
    escalation_group.is_escalation = True
    await session.commit()

    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID, now=NOON)
    await _age(session, result.alert_id, 11, NOON)

    ids = await run_escalations(session, NOON)
    deliveries = [d for d in await _deliveries(session, result.alert_id) if d.kind == DeliveryKind.ESCALATION.value]
    assert sorted(d.chat_id for d in deliveries) == [-900, 2, OWNER_CHAT_ID]
    assert sorted(ids) == sorted(d.id for d in deliveries)
    assert await run_escalations(session, NOON + timedelta(minutes=5)) == []

    alert = await session.get(Alert, result.alert_id)
    text, _ = render_delivery(alert, deliveries[0], await delivery_context(session, deliveries[0]))
    assert "ESKALATSIYA" in text and "11 daqiqa" in text


@pytest.mark.parametrize("case", ["young", "acknowledged", "warning", "disabled"])
async def test_escalation_skips(session, case):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    alert_type = "speeding" if case == "warning" else "crash"
    result = await ingest_alert(session, normalized(alert_type=alert_type), owner_chat_id=OWNER_CHAT_ID, now=NOON)
    await _age(session, result.alert_id, 5 if case == "young" else 30, NOON)
    if case == "acknowledged":
        alert = await session.get(Alert, result.alert_id)
        alert.status = AlertStatus.ACKNOWLEDGED
        await session.commit()
    if case == "disabled":
        await set_setting(session, None, "escalation_minutes", 0)

    assert await run_escalations(session, NOON) == []


async def test_group_language_is_used_for_group_alerts(session):
    group = await add_group(session, -100)
    group.language = "en"
    await session.commit()
    await add_rule(session, group)
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID, now=NOON)
    delivery = (await _deliveries(session, result.alert_id))[0]

    text, _ = render_delivery(await session.get(Alert, result.alert_id), delivery, await delivery_context(session, delivery))

    assert "Vehicle:" in text and "America/Chicago" in text
    assert isinstance(await session.get(Group, group.id), Group)
