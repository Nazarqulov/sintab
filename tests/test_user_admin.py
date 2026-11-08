import pytest
from sqlalchemy import select

from app.core.permissions import Permission
from app.db.models import AuditLog, Depot, Driver, User, UserRole, UserStatus
from app.services import user_admin
from app.services.depots import DepotError, create_depot, delete_depot
from app.services.registration import match_driver_by_phone, normalize_phone
from app.services.user_admin import AdminError
from app.services.users import has_permission
from tests.helpers import add_user


@pytest.fixture
async def people(session):
    owner = await add_user(session, 1, UserRole.OWNER, full_name="Owner")
    admin = await add_user(session, 2, UserRole.ADMIN, full_name="Admin")
    admin2 = await add_user(session, 3, UserRole.ADMIN, full_name="Admin 2")
    dispatcher = await add_user(session, 4, UserRole.DISPATCHER, full_name="Disp")
    pending = await add_user(session, 5, UserRole.PENDING, UserStatus.PENDING, full_name="New", phone="+1")
    return owner, admin, admin2, dispatcher, pending


async def test_admin_cannot_touch_owner_or_other_admin(session, people):
    owner, admin, admin2, *_ = people
    with pytest.raises(AdminError, match="Owner"):
        await user_admin.set_blocked(session, admin, owner, True)
    with pytest.raises(AdminError, match="faqat Owner"):
        await user_admin.set_blocked(session, admin, admin2, True)
    with pytest.raises(AdminError, match="faqat Owner"):
        await user_admin.delete_user(session, admin, admin2)


async def test_owner_can_manage_admin(session, people):
    owner, admin, *_ = people
    await user_admin.set_role(session, owner, admin, UserRole.MANAGER)
    assert admin.role == UserRole.MANAGER


async def test_nobody_can_modify_themselves(session, people):
    owner, admin, *_ = people
    with pytest.raises(AdminError, match="O'zingizni"):
        await user_admin.set_role(session, admin, admin, UserRole.VIEWER)


async def test_only_owner_can_assign_admin_role(session, people):
    owner, admin, _, dispatcher, _ = people
    with pytest.raises(AdminError, match="tayinlay olmaysiz"):
        await user_admin.set_role(session, admin, dispatcher, UserRole.ADMIN)
    with pytest.raises(AdminError, match="tayinlay olmaysiz"):
        await user_admin.set_role(session, owner, dispatcher, UserRole.OWNER)
    await user_admin.set_role(session, owner, dispatcher, UserRole.ADMIN)
    assert dispatcher.role == UserRole.ADMIN


async def test_dispatcher_cannot_manage_users(session, people):
    *_, dispatcher, pending = people
    with pytest.raises(AdminError, match="ruxsat"):
        await user_admin.set_blocked(session, dispatcher, pending, True)


async def test_approve_requires_role_then_activates_and_audits(session, people):
    _, admin, _, _, pending = people
    with pytest.raises(AdminError, match="rolni tanlang"):
        await user_admin.approve(session, admin, pending)

    await user_admin.set_role(session, admin, pending, UserRole.DISPATCHER)
    await user_admin.approve(session, admin, pending)

    assert pending.status == UserStatus.ACTIVE
    assert await has_permission(pending, Permission.ACK_ALERTS)
    log = await session.scalar(select(AuditLog).where(AuditLog.action == "user_approved"))
    assert log.actor_id == admin.id and log.details["role"] == "dispatcher"

    with pytest.raises(AdminError, match="allaqachon"):
        await user_admin.approve(session, admin, pending)


async def test_reject_blocks_and_unblock_returns_to_pending(session, people):
    _, admin, _, _, pending = people
    await user_admin.reject(session, admin, pending)
    assert pending.status == UserStatus.BLOCKED

    await user_admin.set_blocked(session, admin, pending, False)
    assert pending.status == UserStatus.PENDING


async def test_block_removes_all_permissions(session, people):
    _, admin, _, dispatcher, _ = people
    await user_admin.set_blocked(session, admin, dispatcher, True)
    assert not await has_permission(dispatcher, Permission.VIEW_ALERTS)
    await user_admin.set_blocked(session, admin, dispatcher, False)
    assert dispatcher.status == UserStatus.ACTIVE


async def test_depot_toggle_and_global_roles_clear_depots(session, people):
    owner, admin, _, dispatcher, _ = people
    depot = Depot(name="Joliet")
    session.add(depot)
    await session.commit()

    await user_admin.toggle_depot(session, admin, dispatcher, depot.id)
    assert [d.name for d in dispatcher.depots] == ["Joliet"]
    await user_admin.toggle_depot(session, admin, dispatcher, depot.id)
    assert dispatcher.depots == []

    await user_admin.toggle_depot(session, admin, dispatcher, depot.id)
    await user_admin.set_role(session, owner, dispatcher, UserRole.ADMIN)
    assert dispatcher.depots == []


async def test_override_cycle_is_owner_only(session, people):
    owner, admin, _, dispatcher, _ = people
    with pytest.raises(AdminError, match="faqat Owner"):
        await user_admin.cycle_override(session, admin, dispatcher, Permission.EXPORT_REPORTS)

    await user_admin.cycle_override(session, owner, dispatcher, Permission.EXPORT_REPORTS)
    assert await has_permission(dispatcher, Permission.EXPORT_REPORTS)
    await user_admin.cycle_override(session, owner, dispatcher, Permission.EXPORT_REPORTS)
    assert not await has_permission(dispatcher, Permission.EXPORT_REPORTS)
    await user_admin.cycle_override(session, owner, dispatcher, Permission.EXPORT_REPORTS)
    assert dispatcher.permission_overrides == []


async def test_delete_user_keeps_audit_trail(session, people):
    _, admin, _, dispatcher, _ = people
    dispatcher_id = dispatcher.id
    await user_admin.delete_user(session, admin, dispatcher)

    assert await session.get(User, dispatcher_id) is None
    log = await session.scalar(select(AuditLog).where(AuditLog.action == "user_deleted"))
    assert log.details["full_name"] == "Disp"


async def test_list_users_pending_first_and_paginated(session, people):
    users, pages = await user_admin.list_users(session, 0)
    assert users[0].status == UserStatus.PENDING
    assert pages == 1
    users, pages = await user_admin.list_users(session, 99)
    assert users  # oxirgi sahifaga qisiladi


async def test_depot_create_unique_and_delete_unlinks_users(session, people):
    owner, *_ = people
    depot = await create_depot(session, owner, "  Joliet   Yard ")
    assert depot.name == "Joliet Yard"
    with pytest.raises(DepotError, match="allaqachon"):
        await create_depot(session, owner, "joliet yard")

    dispatcher = people[3]
    await user_admin.toggle_depot(session, owner, dispatcher, depot.id)
    await delete_depot(session, owner, depot.id)
    session.expire_all()
    dispatcher = await session.scalar(select(User).where(User.telegram_id == 4))
    assert dispatcher.depots == []


async def test_driver_matched_by_phone_last_ten_digits(session):
    session.add_all([Driver(samsara_id="d1", full_name="Tom", phone="(312) 555-0101"), Driver(samsara_id="d2", phone=None)])
    await session.commit()
    assert (await match_driver_by_phone(session, "+1 312 555 0101")).full_name == "Tom"
    assert await match_driver_by_phone(session, "+1 312 555 9999") is None
    assert normalize_phone("+1 (312) 555-0101") == "+13125550101"


async def test_ambiguous_phone_match_is_not_guessed(session):
    session.add_all([Driver(samsara_id="d1", phone="3125550101"), Driver(samsara_id="d2", phone="+1 312-555-0101")])
    await session.commit()
    assert await match_driver_by_phone(session, "3125550101") is None
