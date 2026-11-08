"""Admin panelidan o'zgartiriladigan tizim sozlamalari (app_settings jadvalida {"v": qiymat})."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting, AuditLog, User

DEFAULTS: dict[str, Any] = {
    "escalation_minutes": 10,        # 0 — eskalatsiya o'chirilgan
    "digest_enabled": True,
    "digest_start_hour": 22,         # kompaniya vaqt zonasida
    "digest_end_hour": 7,
    "webhook_silence_hours": 6,      # 0 — kuzatilmaydi
    "hos_warning_minutes": 60,       # haydash vaqti shundan kam qolsa — ogohlantirish
    "fuel_warning_percent": 20,
    "doc_reminder_days": [30, 14, 7, 1],
    "shift_report_hour": 18,         # kunlik smena xulosasi; -1 — o'chirilgan
}

CHOICES: dict[str, list[Any]] = {
    "escalation_minutes": [0, 5, 10, 15, 30],
    "digest_enabled": [True, False],
    "digest_window": ["22-7", "23-6", "0-8", "21-6"],
    "webhook_silence_hours": [0, 3, 6, 12, 24],
    "hos_warning_minutes": [0, 30, 60, 90, 120],
    "fuel_warning_percent": [0, 10, 15, 20, 25],
    "shift_report_hour": [-1, 16, 17, 18, 19, 20],
}


async def get_setting(session: AsyncSession, key: str) -> Any:
    row = await session.get(AppSetting, key)
    if row is not None and isinstance(row.value, dict) and "v" in row.value:
        return row.value["v"]
    return DEFAULTS[key]


async def set_setting(session: AsyncSession, actor: User | None, key: str, value: Any) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
    session.add(AuditLog(actor_id=actor.id if actor else None, action="setting_changed", target=key, details={"v": value}))
    await session.commit()


async def apply_choice(session: AsyncSession, actor: User, key: str, raw: str) -> None:
    """Admin paneldagi tugma qiymatini tekshirib saqlaydi (faqat CHOICES'dagi qiymatlar)."""
    allowed = {str(v).lower(): v for v in CHOICES[key]}
    if raw.lower() not in allowed:
        raise ValueError(raw)
    value = allowed[raw.lower()]
    if key == "digest_window":
        start, end = (int(x) for x in value.split("-"))
        await set_setting(session, actor, "digest_start_hour", start)
        await set_setting(session, actor, "digest_end_hour", end)
        return
    await set_setting(session, actor, key, value)
