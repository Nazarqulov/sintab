from datetime import UTC, datetime

import pytest
from aiogram.methods import SetMyCommands
from sqlalchemy import select

from app.bot.handlers.personal import SettingsAction
from app.db.models import User, UserRole
from app.services import preferences
from app.services.preferences import PreferenceError
from tests.helpers import add_user
from tests.telegram_fake import callback_update, message_update




def _user(**kw) -> User:
    return User(telegram_id=1, role=UserRole.DISPATCHER, timezone=kw.pop("timezone", "America/Chicago"), **kw)


@pytest.mark.parametrize(
    ("start", "end", "utc_hour", "expected"),
    [
        (22, 6, 5, True),    # Chicago 23:00 (CST, UTC-6)
        (22, 6, 13, False),  # Chicago 07:00
        (22, 6, 11, True),   # Chicago 05:00
        (0, 8, 7, True),     # Chicago 01:00
        (0, 8, 15, False),   # Chicago 09:00
    ],
)
def test_quiet_hours_in_user_timezone_with_midnight_wrap(start, end, utc_hour, expected):
    user = _user(quiet_start=start, quiet_end=end)
    assert preferences.in_quiet_hours(user, datetime(2026, 1, 15, utc_hour, 0, tzinfo=UTC)) is expected


def test_no_quiet_hours_and_mute_state():
    now = datetime(2026, 1, 15, 12, tzinfo=UTC)
    assert not preferences.in_quiet_hours(_user(), now)
    assert not preferences.is_muted(_user(), now)
    assert preferences.is_muted(_user(muted_until=datetime(2026, 1, 15, 13)), now)  # naive = UTC


async def test_preferences_validation(session):
    user = await add_user(session, 1, UserRole.DISPATCHER)
    with pytest.raises(PreferenceError):
        await preferences.set_preference(session, user, "lang", "de")
    with pytest.raises(PreferenceError):
        await preferences.set_preference(session, user, "tz", "Mars/Base")
    with pytest.raises(PreferenceError):
        await preferences.mute(session, user, 0)
    with pytest.raises(PreferenceError):
        await preferences.mute(session, user, 100)
    await preferences.set_preference(session, user, "quiet", "23-7")
    assert (user.quiet_start, user.quiet_end) == (23, 7)
    await preferences.set_preference(session, user, "quiet", "off")
    assert user.quiet_start is None


async def test_language_change_rerenders_and_updates_commands(session, tg):
    await add_user(session, 3001, UserRole.DISPATCHER, full_name="Dave")

    await tg.feed(callback_update(3001, SettingsAction(field="set_lang", value="ru").pack()))

    session.expire_all()
    user = await session.scalar(select(User).where(User.telegram_id == 3001))
    assert user.language == "ru"
    assert "Настройки" in tg.edits()[-1].text
    commands = [r for r in tg.requests if isinstance(r, SetMyCommands)]
    assert commands and any(c.command == "alerts" and c.description == "Последние алерты" for c in commands[-1].commands)


async def test_help_lists_only_allowed_commands(session, tg):
    await add_user(session, 3001, UserRole.VIEWER, full_name="V")
    await add_user(session, 3002, UserRole.OWNER, full_name="O")

    await tg.feed(message_update(3001, "/help"))
    viewer_help = tg.last_text()
    await tg.feed(message_update(3002, "/help"))
    owner_help = tg.last_text()

    assert "/alerts" in viewer_help and "/users" not in viewer_help and "/broadcast" not in viewer_help
    assert "/users" in owner_help and "/broadcast" in owner_help


async def test_guest_help_shows_guest_commands(session, tg):
    await tg.feed(message_update(4001, "/help"))
    assert "/start" in tg.last_text() and "/alerts" not in tg.last_text()


async def test_mute_and_unmute_commands(session, tg):
    await add_user(session, 3001, UserRole.DISPATCHER)
    await tg.feed(message_update(3001, "/mute 4"))
    assert "/unmute" in tg.last_text()
    session.expire_all()
    assert (await session.scalar(select(User).where(User.telegram_id == 3001))).muted_until is not None

    await tg.feed(message_update(3001, "/mute abc"))
    assert "/mute 4" in tg.last_text()

    await tg.feed(message_update(3001, "/unmute"))
    session.expire_all()
    assert (await session.scalar(select(User).where(User.telegram_id == 3001))).muted_until is None


async def test_profile_and_settings_render(session, tg):
    await add_user(session, 3001, UserRole.MANAGER, full_name="Mia", language="en")
    await tg.feed(message_update(3001, "/profile"))
    assert "My profile" in tg.last_text() and "View reports" in tg.last_text()
    await tg.feed(message_update(3001, "/settings"))
    assert "Personal alerts: Off" in tg.last_text()
