from sqlalchemy import select

from app.bot.handlers.groups import GroupAdmin
from app.db.models import AuditLog, Depot, Group, RoutingRule, UserRole, Vehicle
from app.services.alerts.ingest import ingest_alert
from tests.helpers import OWNER_CHAT_ID, add_user, normalized
from tests.telegram_fake import callback_update, message_update

ADMIN = 3002
GROUP_CHAT = -100777


def cb(action: str, group_id: int = 0, arg: str | None = None) -> str:
    return GroupAdmin(action=action, group_id=group_id, arg=arg).pack()


async def _group(session) -> Group | None:
    session.expire_all()
    return await session.scalar(select(Group))


async def test_bind_in_group_and_topic(session, tg):
    await add_user(session, ADMIN, UserRole.ADMIN, full_name="Adm")

    await tg.feed(message_update(ADMIN, "/bind", chat_id=GROUP_CHAT, chat_type="supergroup", chat_title="Dispatch"))
    assert "Dispatch" in tg.last_text() and "/groups" in tg.last_text()
    await tg.feed(message_update(ADMIN, "/bind", chat_id=GROUP_CHAT, chat_type="supergroup", chat_title="Dispatch"))
    assert "allaqachon" in tg.last_text()
    await tg.feed(message_update(ADMIN, "/bind", chat_id=GROUP_CHAT, chat_type="supergroup", chat_title="Dispatch", thread_id=55))

    groups = (await session.scalars(select(Group).order_by(Group.id))).all()
    assert [(g.chat_id, g.topic_id) for g in groups] == [(GROUP_CHAT, None), (GROUP_CHAT, 55)]
    assert await session.scalar(select(AuditLog).where(AuditLog.action == "group_bound")) is not None


async def test_bind_by_non_admin_or_stranger_is_ignored(session, tg):
    await add_user(session, 3001, UserRole.DISPATCHER)
    await tg.feed(message_update(3001, "/bind", chat_id=GROUP_CHAT, chat_type="supergroup"))
    await tg.feed(message_update(4001, "/bind", chat_id=GROUP_CHAT, chat_type="supergroup"))
    assert tg.sent() == []  # guruhda rad javobi bilan chatni to'ldirmaymiz
    assert await _group(session) is None


async def test_rule_wizard_creates_rule_that_routes_alerts(session, tg):
    await add_user(session, ADMIN, UserRole.ADMIN)
    joliet = Depot(name="Joliet")
    session.add_all([joliet, Vehicle(samsara_id="veh-1", name="Truck 1", depot=joliet, tag="reefer")])
    await session.commit()
    joliet_id = joliet.id
    await tg.feed(message_update(ADMIN, "/bind", chat_id=GROUP_CHAT, chat_type="supergroup", chat_title="Joliet"))
    gid = (await _group(session)).id

    await tg.feed(message_update(ADMIN, "/groups"))
    assert "Joliet" in str(tg.sent(ADMIN)[-1].reply_markup)
    tg.reset_throttling()
    for step in [cb("rule", gid), cb("wt", gid, "crash"), cb("ws", gid, "*"), cb("wd", gid, str(joliet_id)), cb("wg", gid, "0")]:
        await tg.feed(callback_update(ADMIN, step))

    session.expire_all()
    rule = await session.scalar(select(RoutingRule))
    assert (rule.alert_type, rule.severity, rule.depot_id, rule.vehicle_tag) == ("crash", None, joliet_id, "reefer")
    assert "#" in tg.edits()[-1].text and "Qoida" in tg.edits()[-1].text

    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    assert len(result.delivery_ids) == 1
    assert await ingest_alert(session, normalized("e2", "speeding"), owner_chat_id=OWNER_CHAT_ID) is not None


async def test_group_settings_and_unbind(session, tg):
    await add_user(session, ADMIN, UserRole.ADMIN)
    await tg.feed(message_update(ADMIN, "/bind", chat_id=GROUP_CHAT, chat_type="supergroup", chat_title="Ops"))
    gid = (await _group(session)).id

    await tg.feed(callback_update(ADMIN, cb("esc", gid)))
    await tg.feed(callback_update(ADMIN, cb("setlang", gid, "en")))
    group = await _group(session)
    assert group.is_escalation is True and group.language == "en"

    await tg.feed(callback_update(ADMIN, cb("unbind", gid)))
    await tg.feed(callback_update(ADMIN, cb("unbindok", gid)))
    assert await _group(session) is None


async def test_manager_without_routing_permission_cannot_start_wizard(session, tg):
    owner = await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    manager = await add_user(session, 3005, UserRole.MANAGER)
    group = Group(chat_id=GROUP_CHAT, title="G")
    session.add(group)
    await session.commit()
    gid = group.id
    assert owner and manager

    await tg.feed(callback_update(3005, cb("rule", gid)))

    assert tg.answers()[-1].text.startswith("⛔")
    assert (await session.scalars(select(RoutingRule))).all() == []
