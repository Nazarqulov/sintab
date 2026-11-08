"""Ko'rish bo'limlari: /alerts, /vehicles, /drivers, /where — har biri foydalanuvchi ko'rish doirasida."""

import logging
from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.alerts import alert_keyboard
from app.bot.keyboards.menu import home_button
from app.bot.states import BrowseSearch
from app.core.config import settings
from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import Alert, AlertStatus, Driver, User, Vehicle
from app.services.alerts.formatter import SEVERITY_ICONS, as_utc, format_alert, format_time, maps_url, safe_zone, type_label
from app.services.depots import DepotError, list_depots, set_vehicle_depot
from app.services.samsara.client import SamsaraAPIError, SamsaraClient
from app.services.samsara.live import LiveStatus, parse_vehicle_stats
from app.services.scope import can_see_alert, scoped_alerts, scoped_drivers, scoped_vehicles
from app.services.users import has_permission

logger = logging.getLogger(__name__)
router = Router(name="browse")

VIEW_ALERTS = {"permission": Permission.VIEW_ALERTS}
VIEW_VEHICLES = {"permission": Permission.VIEW_VEHICLES}
PAGE = 8
SEARCH_LIMIT = 20
STATUS_ICONS = {AlertStatus.NEW: "🆕", AlertStatus.ACKNOWLEDGED: "✅", AlertStatus.RESOLVED: "✔️"}


class AlertList(CallbackData, prefix="alst"):
    flt: str = "open"
    page: int = 0
    vehicle_id: int = 0
    driver_id: int = 0


class AlertView(CallbackData, prefix="alv"):
    alert_id: int


class VehicleNav(CallbackData, prefix="vh"):
    action: str
    vehicle_id: int = 0
    page: int = 0
    arg: int = 0


class DriverNav(CallbackData, prefix="dr"):
    action: str
    driver_id: int = 0
    page: int = 0


def _nav(page: int, pages: int, make) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=make(page - 1)))
    if pages > 1:
        row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=make(page)))
    if page < pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=make(page + 1)))
    return row


async def _edit_or_send(target: Message, text: str, markup: InlineKeyboardMarkup | None, edit: bool) -> None:
    if edit:
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


# ============================ Alertlar ============================

async def alerts_view(session: AsyncSession, user: User, lang: str, flt: str, page: int, vehicle_id: int = 0, driver_id: int = 0):
    stmt = await scoped_alerts(user)
    if flt == "open":
        stmt = stmt.where(Alert.status != AlertStatus.RESOLVED)
    elif flt == "new":
        stmt = stmt.where(Alert.status == AlertStatus.NEW)
    if vehicle_id:
        stmt = stmt.where(Alert.vehicle_id == vehicle_id)
    if driver_id:
        stmt = stmt.where(Alert.driver_id == driver_id)
    stmt = stmt.where(Alert.suppressed.is_(False))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max((total + PAGE - 1) // PAGE, 1)
    page = min(max(page, 0), pages - 1)
    alerts = (await session.scalars(stmt.order_by(Alert.created_at.desc()).offset(page * PAGE).limit(PAGE))).unique().all()

    tz, _ = safe_zone(user.timezone)
    rows = []
    for alert in alerts:
        when = as_utc(alert.happened_at or alert.created_at).astimezone(tz)
        vehicle = alert.vehicle.name if alert.vehicle else "—"
        label = (
            f"{SEVERITY_ICONS.get(alert.severity, '🟢')}{STATUS_ICONS[alert.status]} {when:%d.%m %H:%M} · {vehicle} · "
            f"{type_label(alert.alert_type, lang).split(' ', 1)[-1]}"
        )
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=AlertView(alert_id=alert.id).pack())])

    filters = []
    for key in ("open", "new", "all"):
        mark = "• " if key == flt else ""
        filters.append(InlineKeyboardButton(
            text=f"{mark}{t(f'browse.filter_{key}', lang)}",
            callback_data=AlertList(flt=key, vehicle_id=vehicle_id, driver_id=driver_id).pack(),
        ))
    rows.append(filters)
    rows.append(_nav(page, pages, lambda p: AlertList(flt=flt, page=p, vehicle_id=vehicle_id, driver_id=driver_id).pack()))
    rows.append([home_button(lang)])
    text = t("browse.alerts_title", lang, count=total)
    if not alerts:
        text += "\n\n" + t("browse.nothing", lang)
    return text, InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def show_alerts(target: Message, session: AsyncSession, user: User, lang: str, edit: bool = False, **kw) -> None:
    text, markup = await alerts_view(session, user, lang, kw.get("flt", "open"), kw.get("page", 0),
                                     kw.get("vehicle_id", 0), kw.get("driver_id", 0))
    await _edit_or_send(target, text, markup, edit)


@router.message(Command("alerts"), flags=VIEW_ALERTS)
async def cmd_alerts(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    await show_alerts(message, session, user, lang)


@router.callback_query(AlertList.filter(), flags=VIEW_ALERTS)
async def cb_alert_list(callback: CallbackQuery, callback_data: AlertList, session: AsyncSession, user: User, lang: str) -> None:
    await show_alerts(callback.message, session, user, lang, edit=True, flt=callback_data.flt, page=callback_data.page,
                      vehicle_id=callback_data.vehicle_id, driver_id=callback_data.driver_id)
    await callback.answer()


def detail_markup(alert: Alert, lang: str) -> InlineKeyboardMarkup:
    markup = alert_keyboard(alert, lang)
    rows = list(markup.inline_keyboard) if markup else []
    rows.append([InlineKeyboardButton(text=t("browse.back_to_list", lang), callback_data=AlertList().pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_detail(target: Message, alert: Alert, user: User, lang: str, edit: bool) -> None:
    await _edit_or_send(target, format_alert(alert, user.timezone, lang), detail_markup(alert, lang), edit)


@router.callback_query(AlertView.filter(), flags=VIEW_ALERTS)
async def cb_alert_view(callback: CallbackQuery, callback_data: AlertView, session: AsyncSession, user: User, lang: str) -> None:
    alert = await session.get(Alert, callback_data.alert_id)
    if alert is None or not await can_see_alert(user, alert.driver_id, alert.vehicle.depot_id if alert.vehicle else None):
        await callback.answer(t("action_err.not_found", lang), show_alert=True)
        return
    await render_detail(callback.message, alert, user, lang, edit=True)
    await callback.answer()


# ============================ Mashinalar ============================

async def _vehicle_counts(session: AsyncSession, vehicle_id: int) -> tuple[int, Alert | None]:
    since = datetime.now(UTC) - timedelta(days=7)
    week = await session.scalar(select(func.count(Alert.id)).where(Alert.vehicle_id == vehicle_id, Alert.created_at >= since)) or 0
    last = await session.scalar(select(Alert).where(Alert.vehicle_id == vehicle_id).order_by(Alert.created_at.desc()).limit(1))
    return week, last


def _search_filter(query: str):
    pattern = f"%{query.strip()}%"
    return or_(Vehicle.name.ilike(pattern), Vehicle.plate.ilike(pattern), Vehicle.samsara_id.ilike(pattern))


async def vehicles_view(session: AsyncSession, user: User, lang: str, page: int, query: str | None = None):
    stmt = await scoped_vehicles(user)
    if query:
        stmt = stmt.where(_search_filter(query))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max((total + PAGE - 1) // PAGE, 1)
    page = min(max(page, 0), pages - 1)
    vehicles = (await session.scalars(stmt.order_by(Vehicle.name, Vehicle.id).offset(page * PAGE).limit(PAGE))).all()
    rows = [
        [InlineKeyboardButton(
            text=f"🚚 {v.name or v.samsara_id}{f' · {v.plate}' if v.plate else ''}"[:64],
            callback_data=VehicleNav(action="view", vehicle_id=v.id).pack(),
        )]
        for v in vehicles
    ]
    if not query:
        rows.append(_nav(page, pages, lambda p: VehicleNav(action="list", page=p).pack()))
    rows.append([InlineKeyboardButton(text=t("browse.search", lang), callback_data=VehicleNav(action="search").pack())])
    rows.append([home_button(lang)])
    key = "browse.vehicles_search" if query else "browse.vehicles_title"
    text = t(key, lang, count=total, query=escape(query or ""))
    if not vehicles:
        text += "\n\n" + t("browse.nothing", lang)
    return text, InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def show_vehicles(target: Message, session: AsyncSession, user: User, lang: str, edit: bool = False, page: int = 0, query: str | None = None) -> None:
    text, markup = await vehicles_view(session, user, lang, page, query)
    await _edit_or_send(target, text, markup, edit)


@router.message(Command("vehicles"), flags=VIEW_VEHICLES)
async def cmd_vehicles(message: Message, command: CommandObject, session: AsyncSession, user: User, lang: str) -> None:
    await show_vehicles(message, session, user, lang, query=command.args)


async def _scoped_vehicle(session: AsyncSession, user: User, vehicle_id: int) -> Vehicle | None:
    stmt = (await scoped_vehicles(user)).where(Vehicle.id == vehicle_id)
    return await session.scalar(stmt)


async def vehicle_card(session: AsyncSession, vehicle: Vehicle, user: User, lang: str):
    week, last = await _vehicle_counts(session, vehicle.id)
    location = "—"
    if vehicle.last_location and vehicle.last_location.get("latitude") is not None:
        loc = vehicle.last_location
        place = escape(loc.get("address") or f"{loc['latitude']:.5f}, {loc['longitude']:.5f}")
        location = f'<a href="{escape(maps_url(loc["latitude"], loc["longitude"]), quote=True)}">{place}</a>'
    last_alert = (
        f"{type_label(last.alert_type, lang)} · {format_time(last.happened_at or last.created_at, user.timezone)}" if last else "—"
    )
    text = t(
        "browse.vehicle_card",
        lang,
        name=escape(vehicle.name or vehicle.samsara_id),
        plate=escape(vehicle.plate or "—"),
        tag=escape(vehicle.tag or "—"),
        depot=escape(vehicle.depot.name) if vehicle.depot_id and vehicle.depot else "—",
        location=location,
        seen=format_time(vehicle.last_seen_at, user.timezone),
        week=week,
        last_alert=last_alert,
    )
    vid = vehicle.id
    rows = [
        [InlineKeyboardButton(text=t("browse.btn_live", lang), callback_data=VehicleNav(action="live", vehicle_id=vid).pack()),
         InlineKeyboardButton(text=t("browse.btn_alerts", lang), callback_data=AlertList(flt="all", vehicle_id=vid).pack())],
    ]
    if await has_permission(user, Permission.MANAGE_SETTINGS):
        rows.append([InlineKeyboardButton(text=t("browse.btn_depot", lang), callback_data=VehicleNav(action="depot", vehicle_id=vid).pack())])
    rows.append([InlineKeyboardButton(text=t("common.back", lang), callback_data=VehicleNav(action="list").pack())])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(VehicleNav.filter(), flags=VIEW_VEHICLES)
async def cb_vehicle(
    callback: CallbackQuery, callback_data: VehicleNav, session: AsyncSession, user: User, lang: str, state: FSMContext
) -> None:
    action = callback_data.action
    if action == "list":
        await show_vehicles(callback.message, session, user, lang, edit=True, page=callback_data.page)
        await callback.answer()
        return
    if action == "search":
        await state.set_state(BrowseSearch.vehicle)
        await callback.message.answer(t("browse.ask_query", lang))
        await callback.answer()
        return

    vehicle = await _scoped_vehicle(session, user, callback_data.vehicle_id)
    if vehicle is None:
        await callback.answer(t("vehicle_err.not_found", lang), show_alert=True)
        return
    await session.refresh(vehicle, ["depot"])

    if action == "live":
        await callback.answer()
        await send_live(callback.message, vehicle, user, lang)
        return
    if action in {"depot", "setdepot"}:
        if not await has_permission(user, Permission.MANAGE_SETTINGS):
            await callback.answer(t("access.no_permission", lang), show_alert=True)
            return
        if action == "depot":
            buttons = [[InlineKeyboardButton(text=f"🏢 {d.name}", callback_data=VehicleNav(action="setdepot", vehicle_id=vehicle.id, arg=d.id).pack())]
                       for d in await list_depots(session)]
            buttons.append([InlineKeyboardButton(text=t("groups.no_depot", lang), callback_data=VehicleNav(action="setdepot", vehicle_id=vehicle.id, arg=0).pack())])
            await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
            await callback.answer()
            return
        try:
            vehicle = await set_vehicle_depot(session, user, vehicle.id, callback_data.arg or None)
        except DepotError as error:
            await callback.answer(error.render(lang), show_alert=True)
            return
        await session.refresh(vehicle, ["depot"])
    text, markup = await vehicle_card(session, vehicle, user, lang)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.message(BrowseSearch.vehicle, F.text, ~F.text.startswith("/"), flags=VIEW_VEHICLES)
async def on_vehicle_query(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str) -> None:
    await state.clear()
    await show_vehicles(message, session, user, lang, query=message.text[:64])


# --- Jonli joylashuv (/where) ---

async def fetch_live(vehicle: Vehicle) -> LiveStatus | None:
    if not settings.samsara_api_token:
        return None
    async with SamsaraClient(settings.samsara_api_token, settings.samsara_api_base_url) as client:
        items = await client.get_vehicle_stats(types=("gps", "engineStates", "fuelPercents"), vehicle_ids=[vehicle.samsara_id])
    return parse_vehicle_stats(items[0]) if items else None


async def send_live(target: Message, vehicle: Vehicle, user: User, lang: str) -> None:
    name = escape(vehicle.name or vehicle.samsara_id)
    try:
        live = await fetch_live(vehicle)
    except (SamsaraAPIError, OSError) as error:
        logger.warning("Samsara jonli holat xatosi: %s", error)
        live = None
        await target.answer(t("browse.live_error", lang))
    if live is None or live.latitude is None:
        loc = vehicle.last_location or {}
        if loc.get("latitude") is None:
            await target.answer(t("browse.no_location", lang, name=name))
            return
        await target.answer_location(latitude=loc["latitude"], longitude=loc["longitude"])
        await target.answer(t("browse.last_known", lang, name=name, time=format_time(vehicle.last_seen_at, user.timezone)))
        return
    await target.answer_location(latitude=live.latitude, longitude=live.longitude, heading=int(live.heading) if live.heading else None)
    await target.answer(
        t(
            "browse.live",
            lang,
            name=name,
            address=escape(live.address or f"{live.latitude:.5f}, {live.longitude:.5f}"),
            speed=f"{live.speed_mph:.0f} mph" if live.speed_mph is not None else "—",
            engine=escape(live.engine or "—"),
            fuel=f"{live.fuel_percent:.0f}%" if live.fuel_percent is not None else "—",
            time=format_time(live.gps_time, user.timezone),
        )
    )


@router.message(Command("where"), flags=VIEW_VEHICLES)
async def cmd_where(message: Message, command: CommandObject, session: AsyncSession, user: User, lang: str) -> None:
    if not command.args:
        await message.answer(t("browse.where_usage", lang))
        return
    stmt = (await scoped_vehicles(user)).where(_search_filter(command.args)).order_by(Vehicle.name).limit(SEARCH_LIMIT)
    vehicles = (await session.scalars(stmt)).all()
    if not vehicles:
        await message.answer(t("browse.nothing", lang))
    elif len(vehicles) == 1:
        await send_live(message, vehicles[0], user, lang)
    else:
        rows = [[InlineKeyboardButton(text=f"📍 {v.name or v.samsara_id}", callback_data=VehicleNav(action="live", vehicle_id=v.id).pack())]
                for v in vehicles]
        await message.answer(t("browse.pick_vehicle", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ============================ Haydovchilar ============================

async def drivers_view(session: AsyncSession, user: User, lang: str, page: int, query: str | None = None):
    stmt = await scoped_drivers(user)
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(or_(Driver.full_name.ilike(pattern), Driver.phone.ilike(pattern)))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max((total + PAGE - 1) // PAGE, 1)
    page = min(max(page, 0), pages - 1)
    drivers = (await session.scalars(stmt.order_by(Driver.full_name, Driver.id).offset(page * PAGE).limit(PAGE))).all()
    rows = [
        [InlineKeyboardButton(text=f"👤 {d.full_name or d.samsara_id}"[:64], callback_data=DriverNav(action="view", driver_id=d.id).pack())]
        for d in drivers
    ]
    if not query:
        rows.append(_nav(page, pages, lambda p: DriverNav(action="list", page=p).pack()))
    rows.append([InlineKeyboardButton(text=t("browse.search", lang), callback_data=DriverNav(action="search").pack())])
    rows.append([home_button(lang)])
    key = "browse.drivers_search" if query else "browse.drivers_title"
    text = t(key, lang, count=total, query=escape(query or ""))
    if not drivers:
        text += "\n\n" + t("browse.nothing", lang)
    return text, InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def show_drivers(target: Message, session: AsyncSession, user: User, lang: str, edit: bool = False, page: int = 0, query: str | None = None) -> None:
    text, markup = await drivers_view(session, user, lang, page, query)
    await _edit_or_send(target, text, markup, edit)


@router.message(Command("drivers"), flags=VIEW_VEHICLES)
async def cmd_drivers(message: Message, command: CommandObject, session: AsyncSession, user: User, lang: str) -> None:
    await show_drivers(message, session, user, lang, query=command.args)


async def driver_card(session: AsyncSession, driver: Driver, user: User, lang: str) -> tuple[str, list[list[InlineKeyboardButton]]]:
    since = datetime.now(UTC) - timedelta(days=7)
    counts = (
        await session.execute(
            select(Alert.alert_type, func.count(Alert.id))
            .where(Alert.driver_id == driver.id, Alert.created_at >= since)
            .group_by(Alert.alert_type)
            .order_by(func.count(Alert.id).desc())
        )
    ).all()
    linked = await session.scalar(select(User).where(User.driver_id == driver.id))
    events = ", ".join(f"{type_label(kind, lang).split(' ', 1)[-1]} ({n})" for kind, n in counts[:4]) or "—"
    text = t(
        "browse.driver_card",
        lang,
        name=escape(driver.full_name or driver.samsara_id),
        phone=escape(driver.phone or "—"),
        depot=escape(driver.depot.name) if driver.depot_id and driver.depot else "—",
        bot_user=escape(linked.full_name or str(linked.telegram_id)) if linked else "—",
        week=sum(n for _, n in counts),
        events=events,
    )
    rows = [[InlineKeyboardButton(text=t("browse.btn_alerts", lang), callback_data=AlertList(flt="all", driver_id=driver.id).pack())]]
    return text, rows


@router.callback_query(DriverNav.filter(), flags=VIEW_VEHICLES)
async def cb_driver(
    callback: CallbackQuery, callback_data: DriverNav, session: AsyncSession, user: User, lang: str, state: FSMContext
) -> None:
    if callback_data.action == "list":
        await show_drivers(callback.message, session, user, lang, edit=True, page=callback_data.page)
        await callback.answer()
        return
    if callback_data.action == "search":
        await state.set_state(BrowseSearch.driver)
        await callback.message.answer(t("browse.ask_query", lang))
        await callback.answer()
        return
    driver = await session.scalar((await scoped_drivers(user)).where(Driver.id == callback_data.driver_id))
    if driver is None:
        await callback.answer(t("browse.driver_not_found", lang), show_alert=True)
        return
    await session.refresh(driver, ["depot"])
    text, rows = await driver_card(session, driver, user, lang)
    rows.append([InlineKeyboardButton(text=t("common.back", lang), callback_data=DriverNav(action="list").pack())])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.message(BrowseSearch.driver, F.text, ~F.text.startswith("/"), flags=VIEW_VEHICLES)
async def on_driver_query(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str) -> None:
    await state.clear()
    await show_drivers(message, session, user, lang, query=message.text[:64])
