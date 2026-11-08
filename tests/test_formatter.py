from datetime import UTC, datetime

from app.bot.keyboards.alerts import AlertAction, AlertActionType, alert_keyboard
from app.db.models import Alert, AlertStatus, Driver, User, UserRole, Vehicle
from app.services.alerts.formatter import format_alert, format_time


def make_alert(**overrides) -> Alert:
    values = dict(
        id=7,
        samsara_alert_id="e",
        alert_type="speeding",
        severity="warning",
        message="Vehicle Speed above 75 mph",
        status=AlertStatus.NEW,
        happened_at=datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
        latitude=41.3,
        longitude=69.2,
        address="Tashkent",
        samsara_url="https://cloud.samsara.com/x",
        vehicle_id=3,
    )
    values.update(overrides)
    alert = Alert(**values)
    alert.vehicle = Vehicle(samsara_id="v", name="Truck <12>", plate="01A123")
    alert.driver = Driver(samsara_id="d", full_name="Aziz")
    return alert


def test_message_contains_all_fields_in_local_time():
    text = format_alert(make_alert(), "Asia/Tashkent")
    assert "🟠 <b>WARNING</b> · 🏎 Tezlikni oshirish" in text
    assert "Truck &lt;12&gt; (01A123)" in text  # HTML escape
    assert "Aziz" in text
    assert "08.11.2026 15:00 (Asia/Tashkent)" in text
    assert "google.com/maps" in text and "Tashkent</a>" in text
    assert "🆕 Yangi" in text


def test_unknown_timezone_falls_back_to_utc():
    assert format_time(datetime(2026, 1, 1, 12, 0, tzinfo=UTC), "Not/AZone") == "01.01.2026 12:00 (UTC)"


def test_naive_datetime_from_db_is_treated_as_utc():
    assert format_time(datetime(2026, 1, 1, 12, 0), "Asia/Tashkent") == "01.01.2026 17:00 (Asia/Tashkent)"


def test_acknowledged_status_shows_who_and_assignee():
    alert = make_alert(status=AlertStatus.ACKNOWLEDGED, acknowledged_at=datetime(2026, 11, 8, 10, 5, tzinfo=UTC))
    alert.acknowledged_by_user = User(telegram_id=1, full_name="Dilshod", role=UserRole.DISPATCHER)
    alert.assigned_to_user = User(telegram_id=2, username="bek", role=UserRole.DISPATCHER)
    text = format_alert(alert, "UTC")
    assert "✅ Qabul qilindi — Dilshod (08.11.2026 10:05 (UTC))" in text
    assert "👷 Mas'ul: @bek" in text


def test_keyboard_by_status():
    def labels(alert):
        markup = alert_keyboard(alert)
        return [b.text for row in markup.inline_keyboard for b in row] if markup else []

    assert labels(make_alert()) == ["✅ Qabul qildim", "👤 Menga biriktir", "🗺 Xaritada ko'rish", "🔕 Mute 1 soat"]
    assert labels(make_alert(status=AlertStatus.ACKNOWLEDGED))[0] == "✔️ Yopish"
    assert labels(make_alert(status=AlertStatus.RESOLVED)) == ["🗺 Xaritada ko'rish"]
    assert labels(make_alert(latitude=None, longitude=None, vehicle_id=None)) == ["✅ Qabul qildim", "👤 Menga biriktir"]


def test_callback_data_roundtrip_fits_telegram_limit():
    packed = AlertAction(action=AlertActionType.RESOLVE, alert_id=2_147_483_647).pack()
    assert len(packed.encode()) <= 64
    assert AlertAction.unpack(packed).action == AlertActionType.RESOLVE
