import io
from datetime import UTC, datetime, timedelta

from aiogram.methods import SendDocument
from openpyxl import load_workbook

from app.bot.handlers.reports import ReportAction
from app.db.models import Alert, AlertStatus, Depot, UserRole
from app.services import reports as svc
from app.services.alerts.ingest import ingest_alert
from tests.helpers import OWNER_CHAT_ID, add_user, add_vehicle, normalized
from tests.telegram_fake import callback_update, message_update

NOON = datetime(2026, 1, 15, 18, 0, tzinfo=UTC)  # Chicago 12:00


def test_period_range_uses_user_timezone():
    start, end = svc.period_range("today", "America/Chicago", NOON)
    assert start.astimezone(UTC) == datetime(2026, 1, 15, 6, 0, tzinfo=UTC)  # Chicago yarim tuni = 06:00 UTC
    start, end = svc.period_range("yesterday", "America/Chicago", NOON)
    assert (end - start) == timedelta(days=1)
    start, _ = svc.period_range("7d", "Asia/Tashkent", NOON)
    assert start.hour == 0 and start.tzinfo is not None


async def _seed(session):
    """3 alert: biri tez qabul qilingan va yopilgan, biri ochiq critical, biri warning."""
    owner = await add_user(session, OWNER_CHAT_ID, UserRole.OWNER, full_name="Owner")
    ids = []
    for event_id, kind in [("a", "crash"), ("b", "crash"), ("c", "speeding")]:
        ids.append((await ingest_alert(session, normalized(event_id, kind), owner_chat_id=OWNER_CHAT_ID, now=NOON)).alert_id)
    for alert_id in ids:
        alert = await session.get(Alert, alert_id)
        alert.created_at = NOON - timedelta(hours=1)
    first = await session.get(Alert, ids[0])
    first.status = AlertStatus.RESOLVED
    first.acknowledged_at = NOON - timedelta(minutes=50)
    first.acknowledged_by = owner.id
    first.resolved_at = NOON - timedelta(minutes=30)
    first.resolved_by = owner.id
    first.resolution_reason = "driver_contacted"
    await session.commit()
    return owner


async def test_build_report_counts_and_response_times(session):
    owner = await _seed(session)
    start, end = svc.period_range("today", "America/Chicago", NOON)
    data = svc.build_report(await svc.load_alerts(session, owner, start, end + timedelta(minutes=1)))

    assert data.total == 3
    assert data.by_severity == {"critical": 2, "warning": 1}
    assert data.open_critical == 1
    assert data.acknowledged == 1 and data.resolved == 1
    assert round(data.avg_response_min) == 10 and round(data.avg_resolve_min) == 30
    assert data.by_reason == {"driver_contacted": 1}
    assert data.top_vehicles == [("Truck 1", 3)]
    text = svc.format_report(data, "en", "Today")
    assert "Total: <b>3</b>" in text and "Driver contacted" in text


async def test_report_is_scoped_to_user_depots(session):
    owner = await _seed(session)
    other = Depot(name="Other")
    session.add(other)
    await session.commit()
    scoped = await add_user(session, 3001, UserRole.MANAGER)
    (await scoped.awaitable_attrs.depots).append(other)
    await session.commit()
    start, end = svc.period_range("today", "America/Chicago", NOON)

    assert len(await svc.load_alerts(session, owner, start, end + timedelta(minutes=1))) == 3
    assert await svc.load_alerts(session, scoped, start, end + timedelta(minutes=1)) == []


async def test_exports_have_real_content(session):
    owner = await _seed(session)
    start, end = svc.period_range("today", "America/Chicago", NOON)
    alerts = await svc.load_alerts(session, owner, start, end + timedelta(minutes=1))
    rows = svc.export_rows(alerts, "America/Chicago", "ru")
    data = svc.build_report(alerts)

    csv_bytes = svc.to_csv(rows, "ru")
    assert csv_bytes.startswith(b"\xef\xbb\xbf")  # Excel uchun BOM
    assert "Машина" in csv_bytes.decode("utf-8-sig").splitlines()[0]

    workbook = load_workbook(io.BytesIO(svc.to_xlsx(rows, data, "ru", "Отчёт")))
    sheet = workbook.worksheets[0]
    assert sheet.max_row == 4 and sheet["E2"].value == "Truck 1"
    assert workbook.worksheets[1]["A1"].value == "Отчёт"

    pdf = svc.to_pdf(rows, data, "ru", "Отчёт по алертам — Сегодня")
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


async def test_report_bot_flow_and_export_permission(session, tg):
    await _seed(session)
    await add_user(session, 3001, UserRole.VIEWER, full_name="V")

    await tg.feed(message_update(OWNER_CHAT_ID, "/reports"))
    owner_buttons = [b.text for row in tg.sent(OWNER_CHAT_ID)[-1].reply_markup.inline_keyboard for b in row]
    assert "📊 Excel" in owner_buttons

    await tg.feed(callback_update(OWNER_CHAT_ID, ReportAction(period="7d", fmt="xlsx").pack()))
    document = [r for r in tg.requests if isinstance(r, SendDocument)][-1]
    assert document.document.filename.endswith(".xlsx") and len(document.document.data) > 1000

    await tg.feed(message_update(3001, "/reports"))
    viewer_buttons = [b.text for row in tg.sent(3001)[-1].reply_markup.inline_keyboard for b in row]
    assert "📊 Excel" not in viewer_buttons
    tg.clear()
    await tg.feed(callback_update(3001, ReportAction(period="7d", fmt="xlsx").pack()))
    assert not any(isinstance(r, SendDocument) for r in tg.requests)


async def test_stats_command(session, tg):
    await _seed(session)
    await tg.feed(message_update(OWNER_CHAT_ID, "/stats"))
    assert "Bugungi holat" in tg.last_text()
    await add_vehicle(session, "veh-x")
