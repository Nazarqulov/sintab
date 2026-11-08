from datetime import UTC, date, datetime, timedelta

from aiogram.methods import SendDocument
from aiogram.types import Chat, Message, Update
from aiogram.types import Document as TgDocument
from sqlalchemy import func, select

from app.bot.handlers.documents import DocNav
from app.db.models import Alert, Depot, Document, DocumentOwner, Driver, UserRole, Vehicle
from app.services import documents as svc
from app.services.fleet_jobs import run_document_reminders, run_fuel_check, run_hos_warnings, run_shift_report
from app.services.samsara.hos import format_duration, parse_hos_clock
from tests.helpers import OWNER_CHAT_ID, FakeBot, add_user
from tests.telegram_fake import callback_update, message_update, tg_user

NOW = datetime(2026, 11, 9, 1, 0, tzinfo=UTC)  # Chicago: 8-noyabr, 19:00
HOUR_MS = 3_600_000


def _clock(driver_id: str, status: str, drive_ms: int, shift_ms: int = 10 * HOUR_MS) -> dict:
    return {
        "driver": {"id": driver_id, "name": f"Driver {driver_id}"},
        "currentDutyStatus": {"hosStatusType": status},
        "currentVehicle": {"id": "v1", "name": "Truck 1"},
        "clocks": {
            "drive": {"driveRemainingDurationMs": drive_ms},
            "shift": {"shiftRemainingDurationMs": shift_ms},
            "cycle": {"cycleRemainingDurationMs": 40 * HOUR_MS},
        },
    }


class FakeSamsara:
    def __init__(self, clocks: list[dict] | None = None, stats: list[dict] | None = None) -> None:
        self.clocks, self.stats = clocks or [], stats or []

    async def get_hos_clocks(self, driver_ids=None):
        return self.clocks

    async def get_vehicle_stats(self, types=(), vehicle_ids=None):
        return self.stats


def _file_update(user_id: int, file_name: str, mime_type: str, caption: str | None = None) -> Update:
    message = Message(
        message_id=9000, date=datetime.now(UTC), chat=Chat(id=user_id, type="private"), from_user=tg_user(user_id),
        document=TgDocument(file_id=f"file-{file_name}", file_unique_id=f"u-{file_name}", file_name=file_name,
                            mime_type=mime_type, file_size=1024),
        caption=caption,
    )
    return Update(update_id=9000 + user_id, message=message)


def _doc(owner_type: str, owner_id: int | None, title: str, expires_at: date | None = None, **kw) -> Document:
    return Document(owner_type=owner_type, owner_id=owner_id, doc_type=kw.pop("doc_type", "other"), title=title,
                    file_id=f"f-{title}", file_unique_id=f"u-{title}", file_name=f"{title}.pdf",
                    mime_type="application/pdf", expires_at=expires_at, **kw)


# --- HOS ---

def test_hos_clock_parsing():
    clock = parse_hos_clock(_clock("d1", "driving", 45 * 60_000, shift_ms=3 * HOUR_MS))
    assert clock.is_working and clock.tightest_ms == 45 * 60_000
    assert format_duration(clock.drive_remaining_ms) == "0h 45m" and format_duration(None) == "—"
    assert parse_hos_clock({"clocks": {}}) is None
    assert not parse_hos_clock(_clock("d2", "offDuty", 0)).is_working


async def test_hos_warning_alert_once_per_day(session):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    client = FakeSamsara(clocks=[
        _clock("d-low", "driving", 30 * 60_000),
        _clock("d-ok", "driving", 5 * HOUR_MS),
        _clock("d-off", "offDuty", 10 * 60_000),  # dam olyapti — ogohlantirish shart emas
    ])
    deliveries = await run_hos_warnings(session, client, NOW)
    alerts = (await session.scalars(select(Alert))).all()
    assert [a.alert_type for a in alerts] == ["hos_warning"] and deliveries
    assert "0h 30m" in alerts[0].message

    assert await run_hos_warnings(session, client, NOW + timedelta(hours=1)) == []
    assert await session.scalar(select(func.count(Alert.id))) == 1


# --- Yoqilg'i ---

async def test_fuel_check_updates_vehicles_and_alerts_low_fuel(session):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    session.add_all([Vehicle(samsara_id="v1", name="Truck 1"), Vehicle(samsara_id="v2", name="Truck 2")])
    await session.commit()
    client = FakeSamsara(stats=[
        {"id": "v1", "fuelPercent": {"value": 12}, "gps": {"latitude": 41.88, "longitude": -87.63,
                                                         "reverseGeo": {"formattedLocation": "Chicago, IL"}}},
        {"id": "v2", "fuelPercent": {"value": 80}},
        {"id": "unknown", "fuelPercent": {"value": 5}},
    ])
    await run_fuel_check(session, client, NOW)
    v1, v2 = (await session.scalars(select(Vehicle).order_by(Vehicle.samsara_id))).all()
    assert (v1.fuel_percent, v2.fuel_percent) == (12, 80)
    assert v1.last_location["latitude"] == 41.88
    alerts = (await session.scalars(select(Alert))).all()
    assert [(a.alert_type, a.vehicle_id) for a in alerts] == [("low_fuel", v1.id)]

    await run_fuel_check(session, client, NOW + timedelta(minutes=30))
    assert await session.scalar(select(func.count(Alert.id))) == 1


# --- Hujjatlar: servis ---

def test_file_and_date_validation():
    assert svc.is_allowed_file("bol.pdf", None) and svc.is_allowed_file("loads.XLSX", None)
    assert svc.is_allowed_file(None, "image/jpeg")
    assert not svc.is_allowed_file("virus.exe", "application/x-msdownload")
    assert svc.parse_date("31.12.2027") == date(2027, 12, 31)
    assert svc.parse_date("12/31/2027") == date(2027, 12, 31)
    assert svc.parse_date("2027-12-31") == date(2027, 12, 31)
    assert svc.parse_date("31.02.2027") is None and svc.parse_date("ertaga") is None


async def test_document_access_is_scoped(session):
    north, south = Depot(name="North"), Depot(name="South")
    session.add_all([north, south])
    await session.commit()
    truck_n, truck_s = Vehicle(samsara_id="n", name="N", depot=north), Vehicle(samsara_id="s", name="S", depot=south)
    aziz = Driver(samsara_id="d1", full_name="Aziz", depot=south)
    bek = Driver(samsara_id="d2", full_name="Bek", depot=south)
    session.add_all([truck_n, truck_s, aziz, bek])
    await session.commit()
    owner = await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    disp = await add_user(session, 3001, UserRole.DISPATCHER)
    (await disp.awaitable_attrs.depots).append(north)
    driver_user = await add_user(session, 3002, UserRole.DRIVER, driver_id=aziz.id)
    viewer = await add_user(session, 3003, UserRole.VIEWER)
    await session.commit()

    assert await svc.can_access(session, disp, DocumentOwner.VEHICLE.value, truck_n.id)
    assert not await svc.can_access(session, disp, DocumentOwner.VEHICLE.value, truck_s.id)
    assert await svc.can_access(session, driver_user, DocumentOwner.DRIVER.value, aziz.id)
    assert not await svc.can_access(session, driver_user, DocumentOwner.DRIVER.value, bek.id)
    assert not await svc.can_access(session, driver_user, DocumentOwner.COMPANY.value, None)

    meta = dict(file_id="f", file_unique_id="u", file_name="cdl.pdf", mime_type="application/pdf", file_size=10)
    cdl = await svc.add_document(session, driver_user, owner_type="driver", owner_id=aziz.id, doc_type="cdl",
                                 title="CDL", expires_at=date(2027, 1, 1), **meta)
    try:
        await svc.add_document(session, driver_user, owner_type="driver", owner_id=bek.id, doc_type="cdl",
                               title="x", expires_at=None, **meta)
        raise AssertionError("boshqa haydovchiga hujjat qo'shib bo'lmasligi kerak")
    except svc.DocumentError:
        pass
    session.add_all([_doc("vehicle", truck_s.id, "south-reg"), _doc("company", None, "insurance")])
    await session.commit()

    titles = lambda docs: sorted(d.title for d in docs)  # noqa: E731
    assert titles((await svc.list_documents(session, disp, None, None, 0))[0]) == ["insurance"]
    assert titles((await svc.list_documents(session, owner, None, None, 0))[0]) == ["CDL", "insurance", "south-reg"]
    assert titles((await svc.list_documents(session, driver_user, None, None, 0))[0]) == ["CDL"]

    # Ko'ruvchi (viewer) boshqaning hujjatini o'chira olmaydi, egasi esa o'chira oladi.
    for actor in (viewer,):
        try:
            await svc.delete_document(session, actor, cdl.id)
            raise AssertionError("ruxsatsiz o'chirish")
        except svc.DocumentError:
            pass
    await svc.delete_document(session, driver_user, cdl.id)
    assert await session.get(Document, cdl.id) is None


# --- Hujjatlar: eslatmalar ---

async def test_document_reminders_once_per_stage(session):
    aziz = Driver(samsara_id="d1", full_name="Aziz")
    session.add(aziz)
    await session.commit()
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await add_user(session, 3002, UserRole.DRIVER, driver_id=aziz.id)
    await add_user(session, 3003, UserRole.DISPATCHER)
    today = date(2026, 11, 8)
    session.add_all([
        _doc("driver", aziz.id, "Aziz CDL", today + timedelta(days=10), doc_type="cdl"),
        _doc("company", None, "Insurance", today - timedelta(days=2), doc_type="insurance"),
        _doc("company", None, "IFTA", today + timedelta(days=100), doc_type="ifta"),
    ])
    await session.commit()

    bot = FakeBot()
    assert await run_document_reminders(session, bot, NOW) == 3  # owner x2, haydovchi x1
    by_chat = {}
    for message in bot.sent:
        by_chat.setdefault(message["chat_id"], []).append(message["text"])
    assert len(by_chat[OWNER_CHAT_ID]) == 2 and 3003 not in by_chat
    assert "Aziz CDL" in by_chat[3002][0] and "10" in by_chat[3002][0]
    assert any("Insurance" in text and "⛔️" in text for text in by_chat[OWNER_CHAT_ID])

    assert await run_document_reminders(session, FakeBot(), NOW + timedelta(hours=1)) == 0
    # 7 kun qolganda — yangi bosqich, yana bir marta eslatadi.
    assert await run_document_reminders(session, FakeBot(), NOW + timedelta(days=3)) == 2


# --- Smena xulosasi ---

async def test_shift_report_after_configured_hour_once(session):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await add_user(session, 3004, UserRole.MANAGER)
    await add_user(session, 3005, UserRole.DISPATCHER)
    bot = FakeBot()
    assert await run_shift_report(session, bot, datetime(2026, 11, 8, 20, 0, tzinfo=UTC)) == 0  # Chicago 14:00
    assert await run_shift_report(session, bot, NOW) == 2
    assert {m["chat_id"] for m in bot.sent} == {OWNER_CHAT_ID, 3004}
    assert "Smena xulosasi" in bot.sent[0]["text"]
    assert await run_shift_report(session, bot, NOW + timedelta(hours=1)) == 0


# --- Bot oqimlari ---

async def test_upload_and_download_document_flow(session, tg):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await tg.feed(message_update(OWNER_CHAT_ID, "/docs"))
    assert "Hujjatlar" in tg.last_text()

    await tg.feed(callback_update(OWNER_CHAT_ID, DocNav(action="add").pack()))
    await tg.feed(callback_update(OWNER_CHAT_ID, DocNav(action="add", owner="company").pack()))
    await tg.feed(callback_update(OWNER_CHAT_ID, DocNav(action="type", arg="insurance").pack()))
    assert "Sug'urta" in tg.last_text()

    await tg.feed(_file_update(OWNER_CHAT_ID, "virus.exe", "application/x-msdownload"))
    assert "qabul qilinmaydi" in tg.last_text()
    await tg.feed(_file_update(OWNER_CHAT_ID, "policy.pdf", "application/pdf", caption="Progressive 2027"))
    assert "muddatini" in tg.last_text()
    await tg.feed(message_update(OWNER_CHAT_ID, "ertaga"))
    assert "Sana noto'g'ri" in tg.last_text()
    await tg.feed(message_update(OWNER_CHAT_ID, "31.12.2027"))
    assert "Progressive 2027" in tg.last_text()

    document = await session.scalar(select(Document))
    assert (document.owner_type, document.doc_type, document.expires_at) == ("company", "insurance", date(2027, 12, 31))
    tg.reset_throttling()
    await tg.feed(callback_update(OWNER_CHAT_ID, DocNav(action="get", doc_id=document.id).pack()))
    sent = [r for r in tg.requests if isinstance(r, SendDocument)]
    assert sent and sent[-1].document == "file-policy.pdf" and "31.12.2027" in sent[-1].caption


async def test_driver_docs_are_limited_to_own(session, tg):
    aziz, bek = Driver(samsara_id="d1", full_name="Aziz"), Driver(samsara_id="d2", full_name="Bek")
    session.add_all([aziz, bek])
    await session.commit()
    await add_user(session, 3002, UserRole.DRIVER, driver_id=aziz.id)
    session.add(_doc("driver", bek.id, "bek-cdl"))
    await session.commit()
    other = await session.scalar(select(Document))

    await tg.feed(message_update(3002, "/docs"))
    labels = [b.text for row in tg.sent(3002)[-1].reply_markup.inline_keyboard for b in row]
    assert any("Mening hujjatlarim" in label for label in labels)
    await tg.feed(callback_update(3002, DocNav(action="get", doc_id=other.id).pack()))
    assert tg.answers()[-1].show_alert and not any(isinstance(r, SendDocument) for r in tg.requests)


async def test_fuel_and_hos_commands(session, tg):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    session.add_all([
        Vehicle(samsara_id="v1", name="Full Truck", fuel_percent=90, fuel_updated_at=NOW),
        Vehicle(samsara_id="v2", name="Empty Truck", fuel_percent=8, fuel_updated_at=NOW),
    ])
    await session.commit()
    await tg.feed(message_update(OWNER_CHAT_ID, "/fuel"))
    text = tg.last_text()
    assert "— 2" in text and text.index("Empty Truck") < text.index("Full Truck") and "🔴" in text
    await tg.feed(message_update(OWNER_CHAT_ID, "/hos"))
    assert "SAMSARA_API_TOKEN" in tg.last_text()  # testda token yo'q
