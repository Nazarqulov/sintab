"""/hos (haydovchilarning HOS soatlari) va /fuel (yoqilg'i darajasi)."""

import logging
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.menu import home_button
from app.core.config import settings
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import User, UserRole, Vehicle
from app.services.app_settings import get_setting
from app.services.alerts.formatter import format_time
from app.services.samsara.client import SamsaraAPIError, SamsaraClient
from app.services.samsara.hos import HosClock, format_duration, parse_hos_clock
from app.services.scope import depot_scope, scoped_drivers, scoped_vehicles

logger = logging.getLogger(__name__)
router = Router(name="fleet")

VIEW_VEHICLES = {"permission": Permission.VIEW_VEHICLES}
MAX_ROWS = 25


class FleetAction(CallbackData, prefix="fl"):
    view: str


def _refresh_markup(view: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("fleet.refresh", lang), callback_data=FleetAction(view=view).pack())],
        [home_button(lang)],
    ])


# --- HOS ---

async def fetch_clocks(driver_samsara_ids: list[str] | None = None) -> list[HosClock]:
    async with SamsaraClient(settings.samsara_api_token, settings.samsara_api_base_url) as client:
        items = await client.get_hos_clocks(driver_samsara_ids)
    return [clock for clock in (parse_hos_clock(i) for i in items) if clock]


async def hos_text(session: AsyncSession, user: User, lang: str) -> str:
    if not settings.samsara_api_token:
        return t("fleet.no_token", lang)
    allowed = {d.samsara_id: d for d in (await session.scalars(await scoped_drivers(user))).all()}
    if user.role == UserRole.DRIVER and not allowed:
        return t("fleet.driver_not_linked", lang)
    try:
        clocks = await fetch_clocks(list(allowed) if user.role == UserRole.DRIVER else None)
    except (SamsaraAPIError, OSError) as error:
        logger.warning("HOS olinmadi: %s", error)
        return t("fleet.samsara_error", lang)
    # Cheklovsiz foydalanuvchi bazaga hali sinxronlanmagan haydovchilarni ham ko'rsin.
    if user.role == UserRole.DRIVER or await depot_scope(user) is not None:
        clocks = [c for c in clocks if c.driver_samsara_id in allowed]
    threshold = (await get_setting(session, "hos_warning_minutes")) * 60_000
    clocks.sort(key=lambda c: (c.tightest_ms is None, c.tightest_ms or 0))
    lines = [t("fleet.hos_title", lang, count=len(clocks)), ""]
    for clock in clocks[:MAX_ROWS]:
        icon = "⚠️" if threshold and clock.tightest_ms is not None and clock.tightest_ms <= threshold else "🟢"
        if not clock.is_working:
            icon = "💤"
        lines.append(t(
            "fleet.hos_line", lang,
            icon=icon,
            name=escape(clock.driver_name or clock.driver_samsara_id),
            status=escape(clock.duty_status or "—"),
            drive=format_duration(clock.drive_remaining_ms),
            shift=format_duration(clock.shift_remaining_ms),
            cycle=format_duration(clock.cycle_remaining_ms),
            vehicle=escape(clock.vehicle_name or "—"),
        ))
    if not clocks:
        lines.append(t("browse.nothing", lang))
    return "\n".join(lines)


async def show_hos(target: Message, session: AsyncSession, user: User, lang: str, edit: bool = False) -> None:
    text, markup = await hos_text(session, user, lang), _refresh_markup("hos", lang)
    await (target.edit_text(text, reply_markup=markup) if edit else target.answer(text, reply_markup=markup))


@router.message(Command("hos"), flags=VIEW_VEHICLES)
async def cmd_hos(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await show_hos(message, session, user, lang)


# --- Yoqilg'i ---

def fuel_bar(percent: float) -> str:
    filled = max(0, min(10, round(percent / 10)))
    return "█" * filled + "░" * (10 - filled)


async def fuel_text(session: AsyncSession, user: User, lang: str) -> str:
    threshold = await get_setting(session, "fuel_warning_percent")
    vehicles = (await session.scalars((await scoped_vehicles(user)).where(Vehicle.fuel_percent.is_not(None)).order_by(Vehicle.fuel_percent))).all()
    lines = [t("fleet.fuel_title", lang, count=len(vehicles)), ""]
    for vehicle in vehicles[:MAX_ROWS]:
        icon = "🔴" if threshold and vehicle.fuel_percent < threshold else "🟢"
        lines.append(
            f"{icon} <code>{fuel_bar(vehicle.fuel_percent)}</code> {vehicle.fuel_percent:.0f}% · "
            f"{escape(vehicle.name or vehicle.samsara_id)} · {format_time(vehicle.fuel_updated_at, user.timezone)[:16]}"
        )
    if not vehicles:
        lines.append(t("fleet.no_fuel_data", lang) if not settings.samsara_api_token else t("fleet.fuel_pending", lang))
    return "\n".join(lines)


async def show_fuel(target: Message, session: AsyncSession, user: User, lang: str, edit: bool = False) -> None:
    text, markup = await fuel_text(session, user, lang), _refresh_markup("fuel", lang)
    await (target.edit_text(text, reply_markup=markup) if edit else target.answer(text, reply_markup=markup))


@router.message(Command("fuel"), flags=VIEW_VEHICLES)
async def cmd_fuel(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await show_fuel(message, session, user, lang)


@router.callback_query(FleetAction.filter(), flags=VIEW_VEHICLES)
async def cb_refresh(callback: CallbackQuery, callback_data: FleetAction, session: AsyncSession, user: User, lang: str) -> None:
    await callback.answer()
    if callback_data.view == "hos":
        await show_hos(callback.message, session, user, lang, edit=True)
    else:
        await show_fuel(callback.message, session, user, lang, edit=True)
