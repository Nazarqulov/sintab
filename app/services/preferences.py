from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import LANGUAGES, LocalizedError
from app.db.models import AuditLog, User
from app.services.alerts.formatter import as_utc, safe_zone

TIMEZONES = [
    "America/Chicago",
    "America/New_York",
    "America/Denver",
    "America/Phoenix",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
    "Asia/Tashkent",
    "Europe/Moscow",
    "UTC",
]
NOTIFY_LEVELS = ["off", "critical", "warning", "all"]
QUIET_PRESETS = {"off": None, "22-6": (22, 6), "22-7": (22, 7), "23-7": (23, 7), "0-8": (0, 8)}
MUTE_HOURS = [1, 4, 8, 24]
MAX_MUTE_HOURS = 72


class PreferenceError(LocalizedError):
    pass


async def set_preference(session: AsyncSession, user: User, field: str, value: str) -> None:
    if field == "lang" and value in LANGUAGES:
        user.language = value
    elif field == "tz" and value in TIMEZONES:
        user.timezone = value
    elif field == "notify" and value in NOTIFY_LEVELS:
        user.notify_level = value
    elif field == "quiet" and value in QUIET_PRESETS:
        preset = QUIET_PRESETS[value]
        user.quiet_start, user.quiet_end = preset if preset else (None, None)
    else:
        raise PreferenceError("settings.invalid")
    session.add(AuditLog(actor_id=user.id, action="preference", target=f"user:{user.id}", details={field: value}))
    await session.commit()


async def mute(session: AsyncSession, user: User, hours: int, now: datetime | None = None) -> datetime:
    if not 1 <= hours <= MAX_MUTE_HOURS:
        raise PreferenceError("mute.invalid_hours", max=MAX_MUTE_HOURS)
    user.muted_until = (now or datetime.now(UTC)) + timedelta(hours=hours)
    await session.commit()
    return user.muted_until


async def unmute(session: AsyncSession, user: User) -> None:
    user.muted_until = None
    await session.commit()


def is_muted(user: User, now: datetime) -> bool:
    return user.muted_until is not None and as_utc(user.muted_until) > now


def in_quiet_hours(user: User, now: datetime) -> bool:
    if user.quiet_start is None or user.quiet_end is None:
        return False
    tz, _ = safe_zone(user.timezone)
    hour = now.astimezone(tz).hour
    start, end = user.quiet_start, user.quiet_end
    return start <= hour < end if start < end else hour >= start or hour < end


def quiet_preset_key(user: User) -> str:
    if user.quiet_start is None or user.quiet_end is None:
        return "off"
    return f"{user.quiet_start}-{user.quiet_end}"
