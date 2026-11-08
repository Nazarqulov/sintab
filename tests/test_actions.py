import pytest
from sqlalchemy import select

from app.bot.keyboards.alerts import AlertActionType
from app.db.models import Alert, AlertMute, AlertStatus, AuditLog, PermissionOverride, UserRole, UserStatus
from app.db.session import AsyncSessionLocal
from app.services.alerts.actions import ActionError, apply_action, refresh_alert_messages
from app.services.alerts.ingest import ingest_alert
from app.services.alerts.sender import AlertSender
from tests.helpers import OWNER_CHAT_ID, FakeBot, add_user, normalized


async def _alert(session) -> int:
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    return result.alert_id


async def _reload(session, alert_id: int) -> Alert:
    session.expire_all()
    return await session.get(Alert, alert_id)


async def test_full_lifecycle_new_ack_resolve(session):
    alert_id = await _alert(session)
    dispatcher = await add_user(session, 1, UserRole.DISPATCHER, full_name="Dilshod")

    await apply_action(session, alert_id, AlertActionType.ACK, dispatcher)
    alert = await _reload(session, alert_id)
    assert alert.status == AlertStatus.ACKNOWLEDGED
    assert alert.acknowledged_by_user.full_name == "Dilshod" and alert.acknowledged_at is not None

    await apply_action(session, alert_id, AlertActionType.RESOLVE, dispatcher)
    alert = await _reload(session, alert_id)
    assert alert.status == AlertStatus.RESOLVED and alert.resolved_by == dispatcher.id

    actions = (await session.scalars(select(AuditLog.action).order_by(AuditLog.id))).all()
    assert actions == ["alert_ack", "alert_resolve"]


async def test_second_ack_is_rejected(session):
    alert_id = await _alert(session)
    first = await add_user(session, 1, UserRole.DISPATCHER)
    second = await add_user(session, 2, UserRole.DISPATCHER)
    await apply_action(session, alert_id, AlertActionType.ACK, first)

    with pytest.raises(ActionError, match="allaqachon"):
        await apply_action(session, alert_id, AlertActionType.ACK, second)
    assert (await _reload(session, alert_id)).acknowledged_by == first.id


async def test_resolve_before_ack_is_rejected(session):
    alert_id = await _alert(session)
    dispatcher = await add_user(session, 1, UserRole.DISPATCHER)
    with pytest.raises(ActionError, match="Avval"):
        await apply_action(session, alert_id, AlertActionType.RESOLVE, dispatcher)


async def test_assign_acknowledges_new_alert(session):
    alert_id = await _alert(session)
    dispatcher = await add_user(session, 1, UserRole.DISPATCHER)

    await apply_action(session, alert_id, AlertActionType.ASSIGN, dispatcher)

    alert = await _reload(session, alert_id)
    assert alert.status == AlertStatus.ACKNOWLEDGED
    assert alert.assigned_to == dispatcher.id and alert.acknowledged_by == dispatcher.id


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (UserRole.VIEWER, UserStatus.ACTIVE),
        (UserRole.MANAGER, UserStatus.ACTIVE),
        (UserRole.DRIVER, UserStatus.ACTIVE),
        (UserRole.DISPATCHER, UserStatus.BLOCKED),
        (UserRole.PENDING, UserStatus.PENDING),
    ],
)
async def test_users_without_permission_cannot_ack(session, role, status):
    alert_id = await _alert(session)
    user = await add_user(session, 1, role, status)
    with pytest.raises(ActionError, match="ruxsat"):
        await apply_action(session, alert_id, AlertActionType.ACK, user)
    assert (await _reload(session, alert_id)).status == AlertStatus.NEW


async def test_unregistered_user_cannot_act(session):
    alert_id = await _alert(session)
    with pytest.raises(ActionError, match="ruxsat"):
        await apply_action(session, alert_id, AlertActionType.ACK, None)


async def test_owner_granted_override_allows_viewer(session):
    alert_id = await _alert(session)
    viewer = await add_user(session, 1, UserRole.VIEWER)
    viewer_id = viewer.id
    session.add(PermissionOverride(user_id=viewer_id, permission="ack_alerts", allowed=True))
    await session.commit()
    session.expire_all()
    viewer = await session.get(type(viewer), viewer_id)

    await apply_action(session, alert_id, AlertActionType.ACK, viewer)
    assert (await _reload(session, alert_id)).status == AlertStatus.ACKNOWLEDGED


async def test_mute_creates_one_hour_mute_for_vehicle_and_type(session):
    alert_id = await _alert(session)
    dispatcher = await add_user(session, 1, UserRole.DISPATCHER)
    dispatcher_id = dispatcher.id

    reply = await apply_action(session, alert_id, AlertActionType.MUTE, dispatcher)

    mute = await session.scalar(select(AlertMute))
    muted = (mute.vehicle_id, mute.alert_type, mute.muted_by)
    expected = ((await _reload(session, alert_id)).vehicle_id, "crash", dispatcher_id)
    assert muted == expected
    assert "1 soat" in str(reply)

    second = await ingest_alert(session, normalized("evt-2"), owner_chat_id=OWNER_CHAT_ID)
    assert second.suppressed


async def test_refresh_edits_every_sent_message(session):
    alert_id = await _alert(session)
    bot = FakeBot()
    sender = AlertSender(bot, AsyncSessionLocal)
    alert = await session.get(Alert, alert_id)
    await session.refresh(alert, ["deliveries"])
    await sender.process(alert.deliveries[0].id)

    dispatcher = await add_user(session, 1, UserRole.DISPATCHER, full_name="Dilshod")
    await apply_action(session, alert_id, AlertActionType.ACK, dispatcher)
    await refresh_alert_messages(bot, session, alert_id)

    assert len(bot.edited) == 1
    edit = bot.edited[0]
    assert edit["chat_id"] == OWNER_CHAT_ID and edit["message_id"] == 501
    assert "Qabul qilindi — Dilshod" in edit["text"]
    buttons = [b.text for row in edit["reply_markup"].inline_keyboard for b in row]
    assert "✔️ Yopish" in buttons and "✅ Qabul qildim" not in buttons
