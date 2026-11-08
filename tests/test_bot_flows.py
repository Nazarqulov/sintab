import pytest
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, SendMessage
from sqlalchemy import select

from app.core.i18n import t
from app.bot.bot_instance import dp
from app.bot.middlewares.throttling import ThrottlingMiddleware
from app.bot.handlers.depots import DepotAdmin
from app.bot.keyboards.users import RegistrationDepot, UserAdmin
from app.db.models import AuditLog, Depot, Driver, User, UserRole, UserStatus
from tests.helpers import add_user
from tests.telegram_fake import FakeSession, callback_update, message_update, own_contact

OWNER_TG = 1000  # conftest'dagi OWNER_TELEGRAM_ID bilan bir xil
NEW_TG = 2001


@pytest.fixture
async def tg(session):
    fake = FakeSession()
    bot = Bot("123456:TEST-TOKEN", session=fake)
    # Router'lar faqat bitta dispatcher'ga ulanadi — modul darajasidagisini qayta ishlatib, holatini tozalaymiz.
    dp.fsm.storage = MemoryStorage()

    def reset_throttling() -> None:
        for middleware in dp.callback_query.outer_middleware:
            if isinstance(middleware, ThrottlingMiddleware):
                middleware._buckets.clear()

    async def feed(update):
        await dp.feed_update(bot, update)

    reset_throttling()
    fake.feed = feed
    fake.reset_throttling = reset_throttling
    return fake


async def _user(session, telegram_id: int) -> User | None:
    session.expire_all()
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


def _cb(action: str, user_id: int, arg: str = "") -> str:
    return UserAdmin(action=action, user_id=user_id, arg=arg).pack()


async def _register(tg, telegram_id: int = NEW_TG, phone: str = "+1 312 555 0101") -> None:
    await tg.feed(message_update(telegram_id, "/start", name="John Smith"))
    await tg.feed(message_update(telegram_id, "John Smith"))
    await tg.feed(message_update(telegram_id, contact=own_contact(telegram_id, phone)))
    await tg.feed(message_update(telegram_id, "Dispatcher"))


async def test_full_registration_creates_pending_user_and_notifies_owner(session, tg):
    await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")
    session.add(Driver(samsara_id="drv-9", full_name="John S.", phone="(312) 555-0101"))
    await session.commit()

    await _register(tg)

    user = await _user(session, NEW_TG)
    assert user.status == UserStatus.PENDING
    assert (user.full_name, user.phone, user.position) == ("John Smith", "+13125550101", "Dispatcher")
    assert user.driver.samsara_id == "drv-9"

    to_owner = tg.sent(OWNER_TG)
    assert len(to_owner) == 1
    assert "Yangi ro'yxatdan o'tish so'rovi" in to_owner[0].text and "John Smith" in to_owner[0].text
    buttons = [b.text for row in to_owner[0].reply_markup.inline_keyboard for b in row]
    assert buttons == ["✅ Tasdiqlash", "❌ Rad etish"]
    assert "So'rovingiz yuborildi" in tg.sent(NEW_TG)[-1].text


async def test_someone_elses_contact_is_rejected(session, tg):
    await tg.feed(message_update(NEW_TG, "/start"))
    await tg.feed(message_update(NEW_TG, "John Smith"))
    await tg.feed(message_update(NEW_TG, contact=own_contact(9999)))

    assert "Faqat o'zingizning raqamingizni" in tg.last_text()
    assert (await _user(session, NEW_TG)).phone is None


async def test_invalid_name_and_text_instead_of_contact(session, tg):
    await tg.feed(message_update(NEW_TG, "/start"))
    await tg.feed(message_update(NEW_TG, "Jo"))
    assert "3–100" in tg.last_text()
    await tg.feed(message_update(NEW_TG, "John Smith"))
    await tg.feed(message_update(NEW_TG, "+13125550101"))
    assert "Raqamimni yuborish" in tg.last_text()


async def test_registration_with_depot_choice(session, tg):
    depot = Depot(name="Joliet")
    session.add(depot)
    await session.commit()

    await _register(tg)
    assert "Qaysi depoda" in tg.last_text()
    await tg.feed(callback_update(NEW_TG, RegistrationDepot(depot_id=depot.id).pack()))

    assert [d.name for d in (await _user(session, NEW_TG)).depots] == ["Joliet"]


async def test_cancel_stops_registration(session, tg):
    await tg.feed(message_update(NEW_TG, "/start"))
    await tg.feed(message_update(NEW_TG, "/cancel"))
    assert "Bekor qilindi" in tg.last_text()
    await tg.feed(message_update(NEW_TG, "John Smith"))
    assert (await _user(session, NEW_TG)).full_name is None


async def test_pending_user_sees_nothing_until_approved(session, tg):
    await _register(tg)
    tg.clear()
    tg.reset_throttling()  # testda xabarlar odamdan ko'ra tez yuboriladi

    await tg.feed(message_update(NEW_TG, "/users"))
    await tg.feed(message_update(NEW_TG, "/start"))

    assert [m.text for m in tg.sent(NEW_TG)] == [t("access.pending"), t("access.pending")]


async def test_role_without_permission_is_denied(session, tg):
    await add_user(session, 3001, UserRole.VIEWER, full_name="V")
    await tg.feed(message_update(3001, "/users"))
    assert tg.last_text() == t("access.no_permission")
    await tg.feed(message_update(3001, "/depots"))
    assert tg.last_text() == t("access.no_permission")


async def test_unregistered_user_in_group_gets_no_reply(session, tg):
    await tg.feed(message_update(4001, "/users", chat_id=-100500, chat_type="supergroup"))
    assert tg.sent() == []


async def test_owner_approves_with_role_and_depots(session, tg):
    owner_id = (await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")).id
    joliet, gary = Depot(name="Joliet"), Depot(name="Gary")
    session.add_all([joliet, gary])
    await session.commit()
    gary_id = gary.id
    await _register(tg)
    target = await _user(session, NEW_TG)
    tg.clear()

    await tg.feed(callback_update(OWNER_TG, _cb("approve", target.id)))
    roles = [b.text for row in tg.edits()[-1].reply_markup.inline_keyboard for b in row]
    assert "🛡 Admin" in roles  # Owner Admin tayinlay oladi

    await tg.feed(callback_update(OWNER_TG, _cb("apsetrole", target.id, "dispatcher")))
    await tg.feed(callback_update(OWNER_TG, _cb("apdep", target.id, str(gary_id))))
    await tg.feed(callback_update(OWNER_TG, _cb("apdone", target.id)))

    target = await _user(session, NEW_TG)
    assert (target.status, target.role) == (UserStatus.ACTIVE, UserRole.DISPATCHER)
    assert sorted(d.name for d in target.depots) == ["Gary"]
    assert "Tasdiqlandi — Owner" in tg.edits()[-1].text
    assert "So'rovingiz tasdiqlandi" in tg.sent(NEW_TG)[-1].text and "Gary" in tg.sent(NEW_TG)[-1].text

    log = await session.scalar(select(AuditLog).where(AuditLog.action == "user_approved"))
    assert log.actor_id == owner_id and log.details == {"role": "dispatcher", "depots": ["Gary"]}

    await tg.feed(message_update(NEW_TG, "/start"))
    assert "Xush kelibsiz" in tg.last_text()


async def test_admin_role_choice_excludes_admin_and_forged_callback_is_refused(session, tg):
    await add_user(session, 3002, UserRole.ADMIN, full_name="Adm")
    await _register(tg)
    target = await _user(session, NEW_TG)

    await tg.feed(callback_update(3002, _cb("approve", target.id)))
    roles = [b.text for row in tg.edits()[-1].reply_markup.inline_keyboard for b in row]
    assert "🛡 Admin" not in roles

    # Tugmada yo'q bo'lsa ham, qo'lda yasalgan callback bilan Admin rolini berishga urinish
    await tg.feed(callback_update(3002, _cb("apsetrole", target.id, "admin")))
    assert tg.answers()[-1].text == "Bu rolni tayinlay olmaysiz."
    assert (await _user(session, NEW_TG)).role == UserRole.PENDING


async def test_viewer_forged_admin_callback_is_refused(session, tg):
    await add_user(session, 3001, UserRole.VIEWER, full_name="V")
    victim = await add_user(session, 3003, UserRole.DISPATCHER, full_name="D")

    await tg.feed(callback_update(3001, _cb("block", victim.id)))

    assert tg.answers()[-1].text == t("access.no_permission")
    assert (await _user(session, 3003)).status == UserStatus.ACTIVE


async def test_reject_blocks_and_notifies(session, tg):
    await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")
    await _register(tg)
    target = await _user(session, NEW_TG)

    await tg.feed(callback_update(OWNER_TG, _cb("reject", target.id)))

    assert (await _user(session, NEW_TG)).status == UserStatus.BLOCKED
    assert "rad etildi" in tg.sent(NEW_TG)[-1].text
    await tg.feed(message_update(NEW_TG, "/start"))
    assert tg.last_text() == t("access.blocked")


async def test_second_admin_clicking_stale_request_is_told(session, tg):
    await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")
    await add_user(session, 3002, UserRole.ADMIN, full_name="Adm")
    await _register(tg)
    target = await _user(session, NEW_TG)
    await tg.feed(callback_update(OWNER_TG, _cb("reject", target.id)))

    await tg.feed(callback_update(3002, _cb("approve", target.id)))

    assert "allaqachon ko'rib chiqilgan" in tg.answers()[-1].text


async def test_users_list_card_block_and_delete(session, tg):
    await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")
    victim = await add_user(session, 3003, UserRole.DISPATCHER, full_name="Dave")

    await tg.feed(message_update(OWNER_TG, "/users"))
    assert "Foydalanuvchilar" in tg.last_text()

    await tg.feed(callback_update(OWNER_TG, _cb("view", victim.id)))
    assert "Dave" in tg.edits()[-1].text
    await tg.feed(callback_update(OWNER_TG, _cb("block", victim.id)))
    assert (await _user(session, 3003)).status == UserStatus.BLOCKED
    assert tg.sent(3003)[-1].text == t("access.blocked")

    await tg.feed(callback_update(OWNER_TG, _cb("delete", victim.id)))
    await tg.feed(callback_update(OWNER_TG, _cb("delok", victim.id)))
    assert await _user(session, 3003) is None


async def test_owner_grants_individual_permission(session, tg):
    await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")
    viewer = await add_user(session, 3001, UserRole.VIEWER, full_name="V")

    await tg.feed(callback_update(OWNER_TG, _cb("perms", viewer.id)))
    await tg.feed(callback_update(OWNER_TG, _cb("perm", viewer.id, "manage_users")))
    tg.clear()

    await tg.feed(message_update(3001, "/users"))
    assert "Foydalanuvchilar" in tg.last_text()


async def test_depot_creation_via_bot(session, tg):
    await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")
    await tg.feed(message_update(OWNER_TG, "/depots"))
    await tg.feed(callback_update(OWNER_TG, DepotAdmin(action="add").pack()))
    await tg.feed(message_update(OWNER_TG, "Joliet Yard"))
    assert "«Joliet Yard» qo'shildi" in tg.last_text()

    await tg.feed(callback_update(OWNER_TG, DepotAdmin(action="add").pack()))
    await tg.feed(message_update(OWNER_TG, "joliet yard"))
    assert "allaqachon" in tg.last_text()
    await tg.feed(message_update(OWNER_TG, "/cancel"))
    assert len((await session.scalars(select(Depot))).all()) == 1


async def test_button_spam_is_throttled(session, tg):
    await add_user(session, OWNER_TG, UserRole.OWNER, full_name="Owner")
    for _ in range(12):
        await tg.feed(callback_update(OWNER_TG, _cb("list", 0)))
    throttled = [a for a in tg.answers() if a.text and "Juda tez" in a.text]
    assert 5 <= len(throttled) <= 7


async def test_alert_buttons_require_active_user(session, tg):
    await tg.feed(callback_update(4001, "al:ack:1", chat_id=-100500))
    answer = tg.answers()[-1]
    assert isinstance(answer, AnswerCallbackQuery) and answer.text == t("access.not_registered")
    assert not any(isinstance(m, SendMessage) for m in tg.requests)
