import os
import time
from datetime import UTC, datetime, timedelta

from aiogram.methods import CopyMessage, SendDocument
from sqlalchemy import select

from app.bot.handlers.admin import AdminNav
from app.bot.handlers.broadcast import BroadcastAction
from app.bot.keyboards.menu import MenuAction
from app.core.config import settings
from app.db.models import Alert, AppSetting, AuditLog, UserRole
from app.services.app_settings import get_setting
from app.services.alerts.ingest import ingest_alert
from app.services.monitoring import OwnerNotifier, check_webhook_silence, last_webhook_at, record_webhook
from tests.helpers import OWNER_CHAT_ID, FakeBot, add_user, normalized
from tests.telegram_fake import callback_update, message_update


def _buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_admin_panel_sections_follow_permissions(session, tg):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await add_user(session, 3002, UserRole.ADMIN)
    await add_user(session, 3001, UserRole.DISPATCHER)

    await tg.feed(message_update(OWNER_CHAT_ID, "/admin"))
    assert "💾 Backup" in _buttons(tg.sent(OWNER_CHAT_ID)[-1].reply_markup)
    await tg.feed(message_update(3002, "/admin"))
    admin_buttons = _buttons(tg.sent(3002)[-1].reply_markup)
    assert "👥 Foydalanuvchilar" in admin_buttons and "💾 Backup" not in admin_buttons
    await tg.feed(message_update(3001, "/admin"))
    assert tg.last_text().startswith("⛔")

    await tg.feed(callback_update(3001, AdminNav(section="audit").pack()))
    assert tg.answers()[-1].text.startswith("⛔")


async def test_status_and_audit(session, tg):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    session.add(AuditLog(action="test_action", target="x"))
    await session.commit()

    await tg.feed(message_update(OWNER_CHAT_ID, "/status"))
    assert "Tizim holati" in tg.last_text() and "token kiritilmagan" in tg.last_text()
    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="audit").pack()))
    assert "test_action" in tg.edits()[-1].text


async def test_system_settings_change_is_validated_and_audited(session, tg):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)

    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="settings", key="escalation_minutes").pack()))
    assert "5min" in _buttons(tg.edits()[-1].reply_markup)
    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="setval", key="escalation_minutes", value="5").pack()))
    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="setval", key="digest_window", value="23-6").pack()))
    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="setval", key="escalation_minutes", value="999").pack()))

    session.expire_all()
    assert await get_setting(session, "escalation_minutes") == 5
    assert (await get_setting(session, "digest_start_hour"), await get_setting(session, "digest_end_hour")) == (23, 6)
    assert tg.answers()[-1].text == "Noto'g'ri qiymat."


async def test_severity_override_cycle_changes_ingest(session, tg):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="sevset", key="speeding").pack()))

    session.expire_all()
    assert (await session.get(AppSetting, "severity_overrides")).value == {"speeding": "info"}
    result = await ingest_alert(session, normalized("x", "speeding"), owner_chat_id=OWNER_CHAT_ID)
    assert (await session.get(Alert, result.alert_id)).severity == "info"


async def test_backup_download_owner_only(session, tg, tmp_path, monkeypatch):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))

    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="backup").pack()))
    assert "hali yo'q" in tg.last_text()

    older, newer = tmp_path / "samsara_bot_20260101.dump", tmp_path / "samsara_bot_20260102.dump"
    older.write_bytes(b"old")
    newer.write_bytes(b"new-backup")
    os.utime(older, (time.time() - 100, time.time() - 100))
    await tg.feed(callback_update(OWNER_CHAT_ID, AdminNav(section="backup").pack()))
    document = [r for r in tg.requests if isinstance(r, SendDocument)][-1]
    assert str(document.document.path).endswith("20260102.dump")


async def test_broadcast_copies_message_to_audience(session, tg):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await add_user(session, 3001, UserRole.DISPATCHER)
    await add_user(session, 3002, UserRole.DRIVER)

    await tg.feed(message_update(OWNER_CHAT_ID, "/broadcast"))
    await tg.feed(message_update(OWNER_CHAT_ID, "Bugun 18:00 da yig'ilish"))
    await tg.feed(callback_update(OWNER_CHAT_ID, BroadcastAction(action="pick", target="role-dispatcher").pack()))
    assert "<b>1</b>" in tg.edits()[-1].text
    await tg.feed(callback_update(OWNER_CHAT_ID, BroadcastAction(action="send", target="role-dispatcher").pack()))

    copies = [r for r in tg.requests if isinstance(r, CopyMessage)]
    assert [c.chat_id for c in copies] == [3001]
    assert "Yuborildi: 1" in tg.edits()[-1].text
    assert await session.scalar(select(AuditLog).where(AuditLog.action == "broadcast")) is not None


async def test_menu_and_reply_buttons(session, tg):
    await add_user(session, 3001, UserRole.VIEWER)
    await tg.feed(message_update(3001, "/menu"))
    menu_buttons = _buttons(tg.sent(3001)[-1].reply_markup)
    assert "🚨 Alertlar" in menu_buttons and "🛡 Admin panel" not in menu_buttons

    await tg.feed(message_update(3001, "🚚 Mashinalar"))
    assert "Mashinalar" in tg.last_text()
    await tg.feed(callback_update(3001, MenuAction(section="admin").pack()))
    assert tg.answers()[-1].text.startswith("⛔")


# --- Monitoring ---

async def test_owner_notifier_cooldown():
    bot = FakeBot()
    clock = [0.0]
    notifier = OwnerNotifier(bot, OWNER_CHAT_ID, cooldown=600, clock=lambda: clock[0])
    assert await notifier.notify("k", "a") is True
    assert await notifier.notify("k", "b") is False
    clock[0] = 601
    assert await notifier.notify("k", "c") is True
    assert len(bot.sent) == 2


async def test_webhook_silence_alerts_once_per_silence(session):
    now = datetime(2026, 1, 15, 18, tzinfo=UTC)
    bot = FakeBot()
    notifier = OwnerNotifier(bot, OWNER_CHAT_ID, cooldown=0)

    assert not await check_webhook_silence(session, notifier, OWNER_CHAT_ID, "America/Chicago", now)  # hali webhook yo'q
    await record_webhook(session, now - timedelta(hours=7))
    assert await last_webhook_at(session) == now - timedelta(hours=7)
    assert await check_webhook_silence(session, notifier, OWNER_CHAT_ID, "America/Chicago", now)
    assert not await check_webhook_silence(session, notifier, OWNER_CHAT_ID, "America/Chicago", now + timedelta(hours=1))
    assert len(bot.sent) == 1 and "6 soatdan beri" in bot.sent[0]["text"]

    await record_webhook(session, now)  # tiklandi, keyin yana jim
    assert not await check_webhook_silence(session, notifier, OWNER_CHAT_ID, "America/Chicago", now + timedelta(hours=1))
    assert await check_webhook_silence(session, notifier, OWNER_CHAT_ID, "America/Chicago", now + timedelta(hours=7))
