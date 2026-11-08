"""Telegram Mini App API (`/api/app/*`). Har so'rov `Authorization: tma <initData>` bilan keladi —
imzo bot token'i bilan tekshiriladi, foydalanuvchi bazadan olinadi va bot'dagi RBAC/ko'rish doirasi
xuddi shunday qo'llanadi (bir xil servislar)."""

import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.commands import sync_user_commands
from app.bot.handlers.fleet import HosUnavailable, load_hos
from app.bot.keyboards.alerts import AlertActionType
from app.bot.notify import send_safe
from app.core.config import settings
from app.core.i18n import CATALOG, DEFAULT_LANGUAGE, LocalizedError, t
from app.core.permissions import Permission
from app.db.models import Alert, AlertStatus, Document, DocumentOwner, Driver, User, UserRole, UserStatus, Vehicle
from app.db.session import AsyncSessionLocal
from app.services import documents as docs
from app.services import reports, user_admin
from app.services.alerts.actions import apply_action, refresh_alert_messages
from app.services.alerts.formatter import RESOLUTION_REASONS, safe_zone, type_label
from app.services.app_settings import get_setting
from app.services.depots import list_depots
from app.services.samsara.hos import HosClock
from app.services.scope import scoped_alerts, scoped_drivers, scoped_vehicles
from app.services.users import display_name, user_permissions
from app.services.webapp_auth import InitDataError, validate_init_data

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/app", tags=["webapp"])

PAGE_SIZE = 30
LIST_LIMIT = 300
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TEXT_PREFIXES = ("webapp.", "alert.type.", "alert.reason.", "role.", "status.", "docs.type.", "docs.owner.", "reports.period.")


# --- Bog'liqliklar ---

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


def telegram_bot() -> Bot:
    from app.bot.bot_instance import bot  # aylanma import bo'lmasligi uchun shu yerda

    return bot


async def current_user(authorization: str | None = Header(default=None), session: AsyncSession = Depends(get_session)) -> User:
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData required")
    try:
        tg_user = validate_init_data(authorization[4:], settings.bot_token)
    except InitDataError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid initData: {error}") from error
    user = await session.scalar(select(User).where(User.telegram_id == tg_user["id"]))
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not an active user")
    return user


async def _require(user: User, permission: Permission) -> None:
    if permission not in await user_permissions(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("access.no_permission", user.language))


def _fail(error: LocalizedError, user: User) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, error.render(user.language))


async def _count(session: AsyncSession, stmt) -> int:
    return await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


# --- Serializatsiya ---

def alert_json(alert: Alert, lang: str) -> dict:
    return {
        "id": alert.id,
        "type": alert.alert_type,
        "type_label": type_label(alert.alert_type, lang),
        "severity": alert.severity,
        "status": alert.status.value,
        "message": alert.message,
        "vehicle": (alert.vehicle.name or alert.vehicle.samsara_id) if alert.vehicle else None,
        "vehicle_id": alert.vehicle_id,
        "driver": (alert.driver.full_name or alert.driver.samsara_id) if alert.driver else None,
        "driver_id": alert.driver_id,
        "happened_at": _iso(alert.happened_at or alert.created_at),
        "address": alert.address,
        "lat": alert.latitude,
        "lon": alert.longitude,
        "samsara_url": alert.samsara_url,
        "acknowledged_by": display_name(alert.acknowledged_by_user) if alert.acknowledged_by_user else None,
        "acknowledged_at": _iso(alert.acknowledged_at),
        "assigned_to": display_name(alert.assigned_to_user) if alert.assigned_to_user else None,
        "resolved_by": display_name(alert.resolved_by_user) if alert.resolved_by_user else None,
        "resolved_at": _iso(alert.resolved_at),
        "resolution_reason": alert.resolution_reason,
        "note": alert.resolution_note,
    }


def vehicle_json(vehicle: Vehicle) -> dict:
    location = vehicle.last_location or {}
    return {
        "id": vehicle.id,
        "name": vehicle.name or vehicle.samsara_id,
        "plate": vehicle.plate,
        "depot": vehicle.depot.name if vehicle.depot_id and vehicle.depot else None,
        "lat": location.get("latitude"),
        "lon": location.get("longitude"),
        "address": location.get("address"),
        "last_seen": _iso(vehicle.last_seen_at),
        "fuel": vehicle.fuel_percent,
        "fuel_at": _iso(vehicle.fuel_updated_at),
    }


def hos_json(clock: HosClock, threshold_ms: int) -> dict:
    return {
        "driver": clock.driver_name or clock.driver_samsara_id,
        "status": clock.duty_status,
        "working": clock.is_working,
        "vehicle": clock.vehicle_name,
        "drive_ms": clock.drive_remaining_ms,
        "shift_ms": clock.shift_remaining_ms,
        "cycle_ms": clock.cycle_remaining_ms,
        "warning": bool(threshold_ms and clock.is_working and clock.tightest_ms is not None and clock.tightest_ms <= threshold_ms),
    }


async def document_json(session: AsyncSession, document: Document, user: User) -> dict:
    return {
        "id": document.id,
        "title": document.title,
        "type": document.doc_type,
        "owner_type": document.owner_type,
        "owner_id": document.owner_id,
        "owner": await docs.owner_label(session, document.owner_type, document.owner_id),
        "expires_at": document.expires_at.isoformat() if document.expires_at else None,
        "file_name": document.file_name,
        "mime_type": document.mime_type,
        "size": document.file_size,
        "uploaded_at": _iso(document.created_at),
        "can_delete": document.uploaded_by == user.id or Permission.MANAGE_SETTINGS in await user_permissions(user),
    }


def user_json(target: User) -> dict:
    return {
        "id": target.id,
        "name": display_name(target),
        "username": target.username,
        "phone": target.phone,
        "position": target.position,
        "role": target.role.value,
        "status": target.status.value,
        "depots": [d.name for d in target.depots],
        "depot_ids": [d.id for d in target.depots],
        "created_at": _iso(target.created_at),
    }


# --- Profil va bosh sahifa ---

async def _sections(user: User) -> list[str]:
    permissions = await user_permissions(user)
    sections = ["home"]
    if Permission.VIEW_ALERTS in permissions:
        sections.append("alerts")
    if Permission.VIEW_VEHICLES in permissions:
        sections += ["map", "fleet"]
    if Permission.VIEW_VEHICLES in permissions or user.role == UserRole.DRIVER:
        sections.append("docs")
    if Permission.VIEW_REPORTS in permissions:
        sections.append("reports")
    if Permission.MANAGE_USERS in permissions:
        sections.append("users")
    return sections


@router.get("/me")
async def me(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    lang = user.language if user.language in CATALOG else DEFAULT_LANGUAGE
    return {
        "id": user.id,
        "name": display_name(user),
        "role": user.role.value,
        "language": lang,
        "timezone": user.timezone,
        "permissions": sorted(p.value for p in await user_permissions(user)),
        "sections": await _sections(user),
        "assignable_roles": [r.value for r in user_admin.assignable_roles(user)],
        "reasons": list(RESOLUTION_REASONS),
        "fuel_threshold": await get_setting(session, "fuel_warning_percent"),
        "doc_types": {o.value: [d.value for d in types] for o, types in docs.OWNER_DOC_TYPES.items() if o != DocumentOwner.ALERT},
        "texts": {k: v for k, v in CATALOG[lang].items() if k.startswith(TEXT_PREFIXES)},
    }


@router.get("/dashboard")
async def dashboard(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    permissions = await user_permissions(user)
    now = datetime.now(UTC)
    result: dict = {}
    if Permission.VIEW_ALERTS in permissions:
        base = await scoped_alerts(user)
        today_start, _ = reports.period_range("today", user.timezone, now)
        week_start, _ = reports.period_range("7d", user.timezone, now)
        week = (await session.scalars(base.where(Alert.created_at >= week_start.astimezone(UTC)))).unique().all()
        tz, _ = safe_zone(user.timezone)
        per_day = Counter((a.created_at if a.created_at.tzinfo else a.created_at.replace(tzinfo=UTC)).astimezone(tz).date() for a in week)
        first = week_start.date()
        result.update(
            open=await _count(session, base.where(Alert.status != AlertStatus.RESOLVED)),
            new=await _count(session, base.where(Alert.status == AlertStatus.NEW)),
            critical_open=await _count(session, base.where(Alert.status != AlertStatus.RESOLVED, Alert.severity == "critical")),
            today=await _count(session, base.where(Alert.created_at >= today_start.astimezone(UTC))),
            week=[{"date": (first + timedelta(days=i)).isoformat(), "count": per_day.get(first + timedelta(days=i), 0)} for i in range(7)],
            recent=[alert_json(a, user.language) for a in (await session.scalars(base.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(6))).unique()],
        )
    if Permission.VIEW_VEHICLES in permissions:
        vehicles = await scoped_vehicles(user)
        threshold = await get_setting(session, "fuel_warning_percent")
        result.update(
            vehicles=await _count(session, vehicles),
            low_fuel=await _count(session, vehicles.where(Vehicle.fuel_percent < threshold)) if threshold else 0,
        )
    if "docs" in await _sections(user):
        today = now.astimezone(safe_zone(user.timezone)[0]).date()
        result["expiring_docs"] = len(await docs.expiring_documents(session, user, today))
    return result


# --- Alertlar ---

async def _scoped_alert(session: AsyncSession, user: User, alert_id: int) -> Alert:
    alert = await session.scalar((await scoped_alerts(user)).where(Alert.id == alert_id))
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("action_err.not_found", user.language))
    return alert


@router.get("/alerts")
async def list_alerts(
    flt: str = Query("open", pattern="^(open|new|resolved|all)$"),
    q: str | None = Query(None, max_length=64),
    severity: str | None = Query(None, pattern="^(critical|warning|info)$"),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require(user, Permission.VIEW_ALERTS)
    stmt = await scoped_alerts(user)
    if flt == "open":
        stmt = stmt.where(Alert.status != AlertStatus.RESOLVED)
    elif flt == "new":
        stmt = stmt.where(Alert.status == AlertStatus.NEW)
    elif flt == "resolved":
        stmt = stmt.where(Alert.status == AlertStatus.RESOLVED)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Alert.vehicle_id.in_(select(Vehicle.id).where(or_(Vehicle.name.ilike(pattern), Vehicle.plate.ilike(pattern)))),
            Alert.driver_id.in_(select(Driver.id).where(Driver.full_name.ilike(pattern))),
            Alert.message.ilike(pattern),
            Alert.alert_type.ilike(pattern),
        ))
    total = await _count(session, stmt)
    items = (await session.scalars(stmt.order_by(Alert.created_at.desc(), Alert.id.desc()).offset(offset).limit(PAGE_SIZE))).unique()
    return {"total": total, "items": [alert_json(a, user.language) for a in items]}


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _require(user, Permission.VIEW_ALERTS)
    alert = await _scoped_alert(session, user, alert_id)
    permissions = await user_permissions(user)
    can_ack = Permission.ACK_ALERTS in permissions
    return {
        **alert_json(alert, user.language),
        "can": {
            "ack": can_ack and alert.status == AlertStatus.NEW,
            "assign": can_ack and alert.status != AlertStatus.RESOLVED,
            "resolve": Permission.RESOLVE_ALERTS in permissions and alert.status == AlertStatus.ACKNOWLEDGED,
        },
    }


class AlertActionIn(BaseModel):
    action: str
    reason: str | None = None


@router.post("/alerts/{alert_id}/action")
async def alert_action(
    alert_id: int,
    body: AlertActionIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    bot: Bot = Depends(telegram_bot),
) -> dict:
    await _scoped_alert(session, user, alert_id)
    try:
        action = AlertActionType(body.action)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown action") from error
    try:
        reply = await apply_action(session, alert_id, action, user, reason=body.reason)
    except LocalizedError as error:
        raise _fail(error, user) from error
    # Telegram'dagi barcha nusxalar (guruhlar, shaxsiy) yangi holatni ko'rsatsin.
    try:
        await refresh_alert_messages(bot, session, alert_id)
    except (TelegramAPIError, OSError) as error:
        logger.warning("Alert #%s xabarlari yangilanmadi: %s", alert_id, error)
    return {"message": reply.render(user.language), "alert": await get_alert(alert_id, user, session)}


# --- Park: mashinalar, haydovchilar, HOS ---

@router.get("/vehicles")
async def list_vehicles(
    q: str | None = Query(None, max_length=64), user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    await _require(user, Permission.VIEW_VEHICLES)
    stmt = await scoped_vehicles(user)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Vehicle.name.ilike(pattern), Vehicle.plate.ilike(pattern)))
    vehicles = (await session.scalars(stmt.order_by(Vehicle.name).limit(LIST_LIMIT))).all()
    for vehicle in vehicles:
        await vehicle.awaitable_attrs.depot
    return {"items": [vehicle_json(v) for v in vehicles]}


@router.get("/drivers")
async def list_drivers(
    q: str | None = Query(None, max_length=64), user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    await _require(user, Permission.VIEW_VEHICLES)
    stmt = await scoped_drivers(user)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Driver.full_name.ilike(pattern), Driver.phone.ilike(pattern)))
    drivers = (await session.scalars(stmt.order_by(Driver.full_name).limit(LIST_LIMIT))).all()
    items = []
    for driver in drivers:
        depot = await driver.awaitable_attrs.depot
        items.append({"id": driver.id, "name": driver.full_name or driver.samsara_id, "phone": driver.phone, "depot": depot.name if depot else None})
    return {"items": items}


@router.get("/hos")
async def hos(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    if user.role != UserRole.DRIVER:
        await _require(user, Permission.VIEW_VEHICLES)
    try:
        clocks, threshold = await load_hos(session, user)
    except HosUnavailable as error:
        return {"items": [], "error": error.render(user.language)}
    return {"items": [hos_json(c, threshold) for c in clocks]}


# --- Hujjatlar ---

async def _require_docs(user: User) -> None:
    if user.role != UserRole.DRIVER:
        await _require(user, Permission.VIEW_VEHICLES)


@router.get("/documents")
async def list_documents(
    owner: str | None = Query(None, pattern="^(company|driver|vehicle|alert)$"),
    owner_id: int | None = None,
    expiring: bool = False,
    page: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_docs(user)
    if expiring:
        today = datetime.now(UTC).astimezone(safe_zone(user.timezone)[0]).date()
        items, pages = await docs.expiring_documents(session, user, today), 1
    else:
        if owner and owner != DocumentOwner.COMPANY.value and owner_id and not await docs.can_access(session, user, owner, owner_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, t("access.no_permission", user.language))
        items, pages = await docs.list_documents(session, user, owner, owner_id, page)
    return {"pages": pages, "items": [await document_json(session, d, user) for d in items]}


@router.post("/documents/{document_id}/send")
async def send_document(
    document_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    bot: Bot = Depends(telegram_bot),
) -> dict:
    """Faylni foydalanuvchining bot bilan chatiga yuboradi — Telegram ichida ochish/saqlash eng ishonchli yo'l."""
    await _require_docs(user)
    try:
        document = await docs.get_document(session, user, document_id)
    except LocalizedError as error:
        raise _fail(error, user) from error
    caption = f"📄 {document.title}"
    try:
        if document.file_name is None and (document.mime_type or "").startswith("image/"):
            await bot.send_photo(user.telegram_id, document.file_id, caption=caption)
        else:
            await bot.send_document(user.telegram_id, document.file_id, caption=caption)
    except TelegramAPIError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, t("webapp.send_failed", user.language)) from error
    return {"message": t("webapp.sent_to_chat", user.language)}


@router.post("/documents")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    owner_type: str = Form("company"),
    owner_id: int | None = Form(None),
    title: str | None = Form(None),
    expires_at: str | None = Form(None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    bot: Bot = Depends(telegram_bot),
) -> dict:
    await _require_docs(user)
    if user.role == UserRole.DRIVER:
        owner_type, owner_id = DocumentOwner.DRIVER.value, user.driver_id
    if not docs.is_allowed_file(file.filename, file.content_type):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("docs.bad_type", user.language))
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, t("webapp.too_large", user.language))
    expiry: date | None = None
    if expires_at:
        expiry = docs.parse_date(expires_at)
        if expiry is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, t("docs.bad_date", user.language))
    if not await docs.can_access(session, user, owner_type, owner_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("access.no_permission", user.language))
    # Fayl Telegram'da saqlanadi: foydalanuvchining o'ziga yuborib file_id olinadi (u nusxani chatda ham ko'radi).
    file_name = (file.filename or "file")[:200]
    try:
        sent = await bot.send_document(
            user.telegram_id, BufferedInputFile(content, filename=file_name), caption=t("webapp.uploaded_copy", user.language)
        )
    except TelegramAPIError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, t("webapp.send_failed", user.language)) from error
    stored = sent.document
    try:
        document = await docs.add_document(
            session, user,
            owner_type=owner_type, owner_id=owner_id, doc_type=doc_type,
            title=(title or file_name).strip()[:255] or file_name,
            file_id=stored.file_id, file_unique_id=stored.file_unique_id, file_name=file_name,
            mime_type=file.content_type, file_size=len(content), expires_at=expiry,
        )
    except LocalizedError as error:
        raise _fail(error, user) from error
    return {"message": t("docs.saved", user.language, title=document.title), "document": await document_json(session, document, user)}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _require_docs(user)
    try:
        await docs.delete_document(session, user, document_id)
    except LocalizedError as error:
        raise _fail(error, user) from error
    return {"message": t("docs.deleted", user.language)}


# --- Hisobotlar ---

@router.get("/reports")
async def report(
    period: str = Query("today", pattern="^(today|yesterday|7d|30d)$"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require(user, Permission.VIEW_REPORTS)
    now = datetime.now(UTC)
    start, end = reports.period_range(period, user.timezone, now)
    data = reports.build_report(await reports.load_alerts(session, user, start, end))
    lang = user.language
    return {
        "label": reports.period_label(period, lang, user.timezone, now),
        "total": data.total,
        "acknowledged": data.acknowledged,
        "resolved": data.resolved,
        "open_critical": data.open_critical,
        "avg_response_min": data.avg_response_min,
        "median_response_min": data.median_response_min,
        "avg_resolve_min": data.avg_resolve_min,
        "by_severity": dict(data.by_severity),
        "by_type": [{"type": k, "label": type_label(k, lang), "count": v} for k, v in data.by_type.most_common()],
        "by_reason": [{"reason": k, "count": v} for k, v in data.by_reason.most_common()],
        "top_vehicles": [{"name": n, "count": c} for n, c in data.top_vehicles],
        "top_drivers": [{"name": n, "count": c} for n, c in data.top_drivers],
        "can_export": Permission.EXPORT_REPORTS in await user_permissions(user),
    }


class ExportIn(BaseModel):
    period: str = "today"
    fmt: str = "xlsx"


@router.post("/reports/send")
async def send_report(
    body: ExportIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    bot: Bot = Depends(telegram_bot),
) -> dict:
    await _require(user, Permission.EXPORT_REPORTS)
    if body.period not in reports.PERIODS or body.fmt not in reports.EXPORT_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad period or format")
    content, filename, title = await reports.build_export(session, user, body.period, body.fmt, user.language, datetime.now(UTC))
    try:
        await bot.send_document(user.telegram_id, BufferedInputFile(content, filename=filename), caption=title)
    except TelegramAPIError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, t("webapp.send_failed", user.language)) from error
    return {"message": t("webapp.sent_to_chat", user.language)}


# --- Foydalanuvchilar (admin) ---

@router.get("/users")
async def list_users(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _require(user, Permission.MANAGE_USERS)
    # Kutayotganlar birinchi, keyin faollar, keyin bloklanganlar (bot'dagi /users kabi, lekin sahifasiz).
    order = {UserStatus.PENDING: 0, UserStatus.ACTIVE: 1, UserStatus.BLOCKED: 2}
    targets = sorted((await session.scalars(select(User))).all(), key=lambda u: (order.get(u.status, 3), (u.full_name or "").lower(), u.id))
    return {
        "items": [user_json(u) for u in targets],
        "depots": [{"id": d.id, "name": d.name} for d in await list_depots(session)],
    }


class UserActionIn(BaseModel):
    action: str  # approve | reject | block | unblock | role | depots
    role: str | None = None
    depot_ids: list[int] | None = None


async def _set_depots(session: AsyncSession, actor: User, target: User, depot_ids: list[int]) -> None:
    current = {d.id for d in target.depots}
    for depot_id in current.symmetric_difference(depot_ids):
        await user_admin.toggle_depot(session, actor, target, depot_id)


@router.post("/users/{user_id}")
async def user_action(
    user_id: int,
    body: UserActionIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    bot: Bot = Depends(telegram_bot),
) -> dict:
    await _require(user, Permission.MANAGE_USERS)
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await target.awaitable_attrs.depots
    notice: tuple[str, dict] | None = None
    try:
        if body.action == "approve":
            await user_admin.set_role(session, user, target, UserRole(body.role or ""))
            if target.role not in user_admin.GLOBAL_ROLES:
                await _set_depots(session, user, target, body.depot_ids or [])
            await user_admin.approve(session, user, target)
            depots = t("common.all", target.language) if target.role in user_admin.GLOBAL_ROLES else (
                ", ".join(d.name for d in target.depots) or "—")
            notice = ("reg.approved", {"role": t(f"role.{target.role.value}", target.language), "depots": depots})
        elif body.action == "reject":
            await user_admin.reject(session, user, target)
            notice = ("reg.rejected", {})
        elif body.action == "block":
            await user_admin.set_blocked(session, user, target, True)
            notice = ("access.blocked", {})
        elif body.action == "unblock":
            await user_admin.set_blocked(session, user, target, False)
            if target.status == UserStatus.ACTIVE:
                notice = ("users.access_restored", {})
        elif body.action == "role":
            await user_admin.set_role(session, user, target, UserRole(body.role or ""))
            notice = ("users.role_changed_notice", {"role": t(f"role.{target.role.value}", target.language)})
        elif body.action == "depots":
            if target.role in user_admin.GLOBAL_ROLES:
                raise user_admin.AdminError("admin_err.global_role_depots")
            await _set_depots(session, user, target, body.depot_ids or [])
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown action")
    except ValueError as error:  # noto'g'ri rol nomi
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("admin_err.role_not_assignable", user.language)) from error
    except LocalizedError as error:
        raise _fail(error, user) from error
    if notice is not None:
        await send_safe(bot, target.telegram_id, t(notice[0], target.language, **notice[1]))
    if body.action != "depots":
        await sync_user_commands(bot, target)
    await session.refresh(target, ["depots"])
    return {"message": t("common.saved", user.language), "user": user_json(target)}
