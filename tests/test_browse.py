from aiogram.methods import EditMessageText, SendLocation
from sqlalchemy import select

from app.bot.handlers import browse
from app.bot.handlers.browse import AlertList, AlertView, DriverNav, VehicleNav
from app.bot.keyboards.alerts import AlertAction, AlertActionType
from app.db.models import Alert, AlertStatus, Depot, Driver, UserRole, Vehicle
from app.services.alerts.ingest import ingest_alert
from app.services.samsara.live import LiveStatus, parse_vehicle_stats
from tests.helpers import OWNER_CHAT_ID, add_user, normalized
from tests.telegram_fake import callback_update, message_update


async def _fleet(session):
    north, south = Depot(name="North"), Depot(name="South")
    session.add_all([north, south])
    await session.commit()
    session.add_all([
        Vehicle(samsara_id="v-n", name="North Truck", plate="IL-100", depot=north,
                last_location={"latitude": 41.8, "longitude": -87.6, "address": "Chicago"}),
        Vehicle(samsara_id="v-s", name="South Truck", depot=south),
        Driver(samsara_id="d-1", full_name="Aziz Karimov", phone="+13125550101"),
    ])
    await session.commit()
    await ingest_alert(session, normalized("n1", "crash", vehicle_samsara_id="v-n", vehicle_name="North Truck", driver_samsara_id="d-1",
                                         driver_name="Aziz Karimov", latitude=41.8, longitude=-87.6, address="Chicago"), owner_chat_id=OWNER_CHAT_ID)
    await ingest_alert(session, normalized("s1", "speeding", vehicle_samsara_id="v-s", vehicle_name="South Truck", driver_samsara_id="d-2"), owner_chat_id=OWNER_CHAT_ID)
    return north, south


def _buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_alert_list_is_scoped_and_filterable(session, tg):
    north, _ = await _fleet(session)
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    disp = await add_user(session, 3001, UserRole.DISPATCHER)
    (await disp.awaitable_attrs.depots).append(north)
    await session.commit()

    await tg.feed(message_update(OWNER_CHAT_ID, "/alerts"))
    assert "— 2" in tg.last_text()
    await tg.feed(message_update(3001, "/alerts"))
    labels = _buttons(tg.sent(3001)[-1].reply_markup)
    assert any("North Truck" in b for b in labels) and not any("South Truck" in b for b in labels)

    alert = await session.scalar(select(Alert).where(Alert.samsara_alert_id == "n1"))
    alert.status = AlertStatus.RESOLVED
    await session.commit()
    await tg.feed(callback_update(3001, AlertList(flt="open").pack()))
    assert "— 0" in tg.edits()[-1].text
    await tg.feed(callback_update(3001, AlertList(flt="all").pack()))
    assert "— 1" in tg.edits()[-1].text


async def test_driver_sees_only_own_alerts(session, tg):
    await _fleet(session)
    driver = await session.scalar(select(Driver).where(Driver.samsara_id == "d-1"))
    await add_user(session, 3005, UserRole.DRIVER, driver_id=driver.id)

    await tg.feed(message_update(3005, "/alerts"))
    labels = _buttons(tg.sent(3005)[-1].reply_markup)
    assert any("North Truck" in b for b in labels) and not any("South Truck" in b for b in labels)


async def test_ack_from_detail_card_rerenders_card(session, tg):
    await _fleet(session)
    await add_user(session, 3001, UserRole.DISPATCHER, full_name="Dave")
    alert_id = (await session.scalar(select(Alert).where(Alert.samsara_alert_id == "n1"))).id

    await tg.feed(callback_update(3001, AlertView(alert_id=alert_id).pack()))
    assert "North Truck" in tg.edits()[-1].text
    await tg.feed(callback_update(3001, AlertAction(action=AlertActionType.ACK, alert_id=alert_id).pack()))

    detail_edit = [e for e in tg.requests if isinstance(e, EditMessageText)][-1]
    assert "Qabul qilindi — Dave" in detail_edit.text
    assert "⬅️ Alertlar ro'yxati" in _buttons(detail_edit.reply_markup)


async def test_hidden_alert_detail_is_refused(session, tg):
    _, south = await _fleet(session)
    disp = await add_user(session, 3001, UserRole.DISPATCHER)
    north = await session.scalar(select(Depot).where(Depot.name == "North"))
    (await disp.awaitable_attrs.depots).append(north)
    await session.commit()
    south_alert = await session.scalar(select(Alert).where(Alert.samsara_alert_id == "s1"))

    await tg.feed(callback_update(3001, AlertView(alert_id=south_alert.id).pack()))
    assert tg.answers()[-1].text == "Alert topilmadi."


async def test_vehicle_search_card_and_depot_assignment(session, tg):
    _, south = await _fleet(session)
    south_id = south.id
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await add_user(session, 3001, UserRole.VIEWER)

    await tg.feed(message_update(3001, "/vehicles il-100"))
    labels = _buttons(tg.sent(3001)[-1].reply_markup)
    assert any("North Truck" in b for b in labels) and not any("South Truck" in b for b in labels)

    vehicle = await session.scalar(select(Vehicle).where(Vehicle.samsara_id == "v-n"))
    vid = vehicle.id
    await tg.feed(callback_update(3001, VehicleNav(action="view", vehicle_id=vid).pack()))
    card = tg.edits()[-1]
    assert "IL-100" in card.text and "Chicago" in card.text
    assert "🏢 Depoga biriktirish" not in _buttons(card.reply_markup)  # viewer'da manage_settings yo'q

    await tg.feed(callback_update(3001, VehicleNav(action="setdepot", vehicle_id=vid, arg=south_id).pack()))
    assert tg.answers()[-1].text.startswith("⛔")

    await tg.feed(callback_update(OWNER_CHAT_ID, VehicleNav(action="setdepot", vehicle_id=vid, arg=south_id).pack()))
    session.expire_all()
    assert (await session.get(Vehicle, vid)).depot_id == south_id


async def test_where_without_token_sends_last_known_location(session, tg):
    await _fleet(session)
    await add_user(session, 3001, UserRole.VIEWER)

    await tg.feed(message_update(3001, "/where north"))

    location = [r for r in tg.requests if isinstance(r, SendLocation)][-1]
    assert (location.latitude, location.longitude) == (41.8, -87.6)
    assert "oxirgi ma'lum joylashuv" in tg.last_text()


async def test_where_with_live_samsara_data(session, tg, monkeypatch):
    await _fleet(session)
    await add_user(session, 3001, UserRole.VIEWER, language="en")

    async def fake_live(vehicle):
        return LiveStatus(latitude=41.9, longitude=-87.7, speed_mph=55, heading=90, address="I-90, Chicago", engine="On", fuel_percent=42)

    monkeypatch.setattr(browse, "fetch_live", fake_live)
    await tg.feed(message_update(3001, "/where North"))

    assert [r for r in tg.requests if isinstance(r, SendLocation)][-1].latitude == 41.9
    assert "55 mph" in tg.last_text() and "42%" in tg.last_text() and "I-90" in tg.last_text()


def test_parse_vehicle_stats():
    status = parse_vehicle_stats({
        "id": "1",
        "gps": {"time": "2026-01-15T18:00:00Z", "latitude": 41.8, "longitude": -87.6, "speedMilesPerHour": 61.5,
                "headingDegrees": 270, "reverseGeo": {"formattedLocation": "Joliet, IL"}},
        "engineState": {"value": "Idle"},
        "fuelPercent": {"value": 37},
    })
    assert (status.speed_mph, status.engine, status.fuel_percent, status.address) == (61.5, "Idle", 37.0, "Joliet, IL")
    assert parse_vehicle_stats({}).latitude is None


async def test_driver_card(session, tg):
    await _fleet(session)
    await add_user(session, 3001, UserRole.VIEWER)
    driver = await session.scalar(select(Driver).where(Driver.samsara_id == "d-1"))

    await tg.feed(callback_update(3001, DriverNav(action="view", driver_id=driver.id).pack()))
    assert "Aziz Karimov" in tg.edits()[-1].text and "+13125550101" in tg.edits()[-1].text
