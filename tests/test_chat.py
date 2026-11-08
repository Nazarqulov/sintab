from datetime import UTC, datetime

from aiogram.methods import CopyMessage, SendMessage, SetMessageReaction
from aiogram.types import Chat, Message, Update
from aiogram.types import Document as TgDocument
from sqlalchemy import select

from app.bot.handlers.browse import DriverNav
from app.bot.handlers.chat import ChatNav
from app.db.models import ChatMessage, Depot, Driver, UserRole, UserStatus
from app.services import chat as svc
from tests.helpers import OWNER_CHAT_ID, add_user
from tests.telegram_fake import callback_update, message_update, tg_user

BOT_ID = 123456  # testdagi token "123456:..." dan


def _message(user_id: int, *, text: str | None = None, document: TgDocument | None = None, caption: str | None = None,
             reply_to: int | None = None) -> Update:
    replied = None
    if reply_to is not None:
        replied = Message(message_id=reply_to, date=datetime.now(UTC), chat=Chat(id=user_id, type="private"),
                          from_user=tg_user(BOT_ID).model_copy(update={"is_bot": True}), text="...")
    message = Message(message_id=7000 + user_id, date=datetime.now(UTC), chat=Chat(id=user_id, type="private"),
                      from_user=tg_user(user_id), text=text, document=document, caption=caption, reply_to_message=replied)
    return Update(update_id=70000 + user_id + (reply_to or 0), message=message)


async def _team(session):
    north, south = Depot(name="North"), Depot(name="South")
    session.add_all([north, south])
    await session.commit()
    aziz = Driver(samsara_id="d1", full_name="Aziz", depot=north)
    bek = Driver(samsara_id="d2", full_name="Bek", depot=south)
    lost = Driver(samsara_id="d3", full_name="NoDepot")
    session.add_all([aziz, bek, lost])
    await session.commit()
    users = {
        "owner": await add_user(session, OWNER_CHAT_ID, UserRole.OWNER, full_name="Owner"),
        "disp_n": await add_user(session, 4001, UserRole.DISPATCHER, full_name="Nodira"),
        "disp_s": await add_user(session, 4002, UserRole.DISPATCHER, full_name="Sardor"),
        "viewer": await add_user(session, 4003, UserRole.VIEWER, full_name="Viktor"),
        "aziz": await add_user(session, 4004, UserRole.DRIVER, full_name="Aziz", driver_id=aziz.id),
        "bek": await add_user(session, 4005, UserRole.DRIVER, full_name="Bek", driver_id=bek.id),
        "lost": await add_user(session, 4006, UserRole.DRIVER, full_name="NoDepot", driver_id=lost.id),
        "blocked": await add_user(session, 4007, UserRole.BLOCKED, status=UserStatus.BLOCKED, full_name="Blocked"),
    }
    (await users["disp_n"].awaitable_attrs.depots).append(north)
    (await users["disp_s"].awaitable_attrs.depots).append(south)
    await session.commit()
    return users, aziz


async def test_chat_rules(session):
    u, _ = await _team(session)
    allowed = lambda a, b: svc.can_chat(session, u[a], u[b])  # noqa: E731
    assert await allowed("aziz", "disp_n") and await allowed("disp_n", "aziz")
    assert not await allowed("aziz", "disp_s")  # boshqa depo dispetcheri
    assert await allowed("aziz", "owner")
    assert not await allowed("aziz", "bek") and not await allowed("aziz", "viewer")
    assert await allowed("lost", "disp_s")  # deposi noma'lum haydovchi yordamsiz qolmaydi
    assert await allowed("disp_n", "disp_s") and await allowed("viewer", "owner")
    assert not await allowed("owner", "blocked") and not await allowed("owner", "owner")

    names = [x.full_name for x in await svc.contacts(session, u["aziz"])]
    assert names == ["Owner", "Nodira"]


async def test_contacts_put_recent_partners_first(session, tg):
    u, _ = await _team(session)
    before = [x.full_name for x in await svc.contacts(session, u["owner"])]
    assert before[:3] == ["Nodira", "Sardor", "Viktor"]
    session.add(ChatMessage(sender_id=u["bek"].id, recipient_id=u["owner"].id, content_type="text", text="hi"))
    await session.commit()
    assert (await svc.contacts(session, u["owner"]))[0].full_name == "Bek"
    assert [x.full_name for x in await svc.contacts(session, u["owner"], "sard")] == ["Sardor"]


async def test_conversation_with_files_and_reply(session, tg):
    u, aziz_driver = await _team(session)
    await tg.feed(message_update(4001, "/chat"))
    labels = [b.text for row in tg.sent(4001)[-1].reply_markup.inline_keyboard for b in row]
    assert any("Aziz" in label for label in labels)

    await tg.feed(callback_update(4001, ChatNav(action="to", uid=u["aziz"].id).pack()))
    assert "Aziz" in tg.last_text()
    await tg.feed(_message(4001, text="Salom, <b>yuk</b> tayyormi?"))
    delivered = tg.sent(4004)[-1]
    assert "Nodira" in delivered.text and "Salom, &lt;b&gt;yuk&lt;/b&gt; tayyormi?" in delivered.text
    assert delivered.reply_markup.inline_keyboard[0][0].callback_data == ChatNav(action="to", uid=u["disp_n"].id).pack()
    assert any(isinstance(r, SetMessageReaction) for r in tg.requests)

    pdf = TgDocument(file_id="bol-file", file_unique_id="bol-u", file_name="BOL.pdf", mime_type="application/pdf")
    await tg.feed(_message(4001, document=pdf, caption="BOL"))
    copy = [r for r in tg.requests if isinstance(r, CopyMessage)][-1]
    assert copy.chat_id == 4004 and "Nodira" in copy.caption and "BOL" in copy.caption

    logged = (await session.scalars(select(ChatMessage).order_by(ChatMessage.id))).all()
    assert [(m.content_type, m.file_name) for m in logged] == [("text", None), ("document", "BOL.pdf")]

    # Haydovchi suhbat rejimisiz, yetkazilgan xabarga oddiy reply qiladi.
    tg.clear()
    await tg.feed(_message(4004, text="Ha, tayyor", reply_to=logged[0].delivered_message_id))
    answer = tg.sent(4001)[-1]
    assert "Aziz" in answer.text and "Ha, tayyor" in answer.text

    # Boshqa depo dispetcheriga yozib bo'lmaydi.
    await tg.feed(callback_update(4004, ChatNav(action="to", uid=u["disp_s"].id).pack()))
    assert tg.answers()[-1].show_alert


async def test_reply_keyboard_button_leaves_conversation(session, tg):
    u, _ = await _team(session)
    await tg.feed(callback_update(4001, ChatNav(action="to", uid=u["aziz"].id).pack()))
    tg.clear()
    await tg.feed(message_update(4001, "⚙️ Sozlamalar"))
    assert not tg.sent(4004)
    await tg.feed(message_update(4001, "oddiy matn"))
    assert not tg.sent(4004)  # suhbat rejimi tugagan


async def test_driver_card_has_write_button(session, tg):
    u, aziz_driver = await _team(session)
    await tg.feed(callback_update(4001, DriverNav(action="view", driver_id=aziz_driver.id).pack()))
    markup = tg.edits()[-1].reply_markup
    assert ChatNav(action="to", uid=u["aziz"].id).pack() in [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_end_button_and_cancel(session, tg):
    u, _ = await _team(session)
    await tg.feed(callback_update(4001, ChatNav(action="to", uid=u["aziz"].id).pack()))
    await tg.feed(callback_update(4001, ChatNav(action="end").pack()))
    tg.clear()
    await tg.feed(message_update(4001, "yana"))
    assert not [m for m in tg.requests if isinstance(m, SendMessage) and m.chat_id == 4004]
