"""Hujjatlar: Telegram fayl ID'si bilan saqlanadi (fayl Telegram serverida), bazada — metama'lumot va muddat."""

import re
from datetime import date, datetime, timedelta
from pathlib import PurePath

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import LocalizedError
from app.core.permissions import Permission
from app.db.models import Alert, AuditLog, Document, DocumentOwner, DocumentType, Driver, User, UserRole, Vehicle
from app.services.scope import can_see_alert, depot_scope, scoped_drivers, scoped_vehicles
from app.services.users import has_permission

ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".docx", ".doc", ".txt", ".jpg", ".jpeg", ".png", ".heic", ".webp"}
ALLOWED_MIME_PREFIXES = ("image/",)
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/csv",
    "text/plain",
}
# Muddatli (eslatma beriladigan) hujjat turlari.
EXPIRING_TYPES = {
    DocumentType.CDL, DocumentType.MEDICAL_CARD, DocumentType.INSURANCE,
    DocumentType.REGISTRATION, DocumentType.IFTA, DocumentType.ANNUAL_INSPECTION,
}
OWNER_DOC_TYPES = {
    DocumentOwner.DRIVER: [DocumentType.CDL, DocumentType.MEDICAL_CARD, DocumentType.BOL, DocumentType.POD,
                           DocumentType.ACCIDENT_REPORT, DocumentType.OTHER],
    DocumentOwner.VEHICLE: [DocumentType.REGISTRATION, DocumentType.INSURANCE, DocumentType.ANNUAL_INSPECTION,
                            DocumentType.IFTA, DocumentType.REPAIR, DocumentType.OTHER],
    DocumentOwner.ALERT: [DocumentType.ACCIDENT_REPORT, DocumentType.REPAIR, DocumentType.BOL, DocumentType.POD,
                          DocumentType.OTHER],
    DocumentOwner.COMPANY: [DocumentType.INSURANCE, DocumentType.IFTA, DocumentType.RATE_CONFIRMATION,
                            DocumentType.REPORT, DocumentType.OTHER],
}
PAGE_SIZE = 8


class DocumentError(LocalizedError):
    pass


def is_allowed_file(file_name: str | None, mime_type: str | None) -> bool:
    if mime_type and (mime_type in ALLOWED_MIME_TYPES or mime_type.startswith(ALLOWED_MIME_PREFIXES)):
        return True
    return bool(file_name) and PurePath(file_name).suffix.lower() in ALLOWED_EXTENSIONS


def parse_date(text: str) -> date | None:
    """2027-03-15, 15.03.2027 (o'zbek/rus) yoki 03/15/2027 (AQSh)."""
    text = text.strip()
    for pattern, fmt in ((r"\d{4}-\d{1,2}-\d{1,2}", "%Y-%m-%d"), (r"\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y"),
                         (r"\d{1,2}/\d{1,2}/\d{4}", "%m/%d/%Y")):
        if re.fullmatch(pattern, text):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                return None
    return None


async def can_access(session: AsyncSession, user: User, owner_type: str, owner_id: int | None) -> bool:
    """Hujjat egasini (kompaniya/haydovchi/mashina/alert) foydalanuvchi ko'ra oladimi."""
    if user.role == UserRole.DRIVER:
        if owner_type == DocumentOwner.DRIVER.value:
            return user.driver_id is not None and owner_id == user.driver_id
        if owner_type == DocumentOwner.ALERT.value and owner_id:
            alert = await session.get(Alert, owner_id)
            return alert is not None and user.driver_id is not None and alert.driver_id == user.driver_id
        return False
    if not await has_permission(user, Permission.VIEW_VEHICLES):
        return False
    if owner_type == DocumentOwner.COMPANY.value:
        return True
    if owner_type == DocumentOwner.VEHICLE.value:
        return await session.scalar((await scoped_vehicles(user)).where(Vehicle.id == owner_id)) is not None
    if owner_type == DocumentOwner.DRIVER.value:
        return await session.scalar((await scoped_drivers(user)).where(Driver.id == owner_id)) is not None
    if owner_type == DocumentOwner.ALERT.value and owner_id:
        alert = await session.get(Alert, owner_id)
        return alert is not None and await can_see_alert(
            user, alert.driver_id, alert.vehicle.depot_id if alert.vehicle else None
        )
    return False


async def owner_label(session: AsyncSession, owner_type: str, owner_id: int | None) -> str:
    if owner_type == DocumentOwner.DRIVER.value and owner_id:
        driver = await session.get(Driver, owner_id)
        return (driver.full_name or driver.samsara_id) if driver else "?"
    if owner_type == DocumentOwner.VEHICLE.value and owner_id:
        vehicle = await session.get(Vehicle, owner_id)
        return (vehicle.name or vehicle.samsara_id) if vehicle else "?"
    if owner_type == DocumentOwner.ALERT.value and owner_id:
        return f"Alert #{owner_id}"
    return "🏢"


async def add_document(
    session: AsyncSession,
    actor: User,
    *,
    owner_type: str,
    owner_id: int | None,
    doc_type: str,
    title: str,
    file_id: str,
    file_unique_id: str,
    file_name: str | None,
    mime_type: str | None,
    file_size: int | None,
    expires_at: date | None,
) -> Document:
    if not await can_access(session, actor, owner_type, owner_id):
        raise DocumentError("access.no_permission")
    if doc_type not in {d.value for d in DocumentType}:
        raise DocumentError("settings.invalid")
    if not is_allowed_file(file_name, mime_type):
        raise DocumentError("docs.bad_type")
    document = Document(
        owner_type=owner_type,
        owner_id=owner_id,
        doc_type=doc_type,
        title=title[:255],
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_name=file_name,
        mime_type=mime_type,
        file_size=file_size,
        expires_at=expires_at,
        uploaded_by=actor.id,
    )
    session.add(document)
    await session.flush()
    session.add(AuditLog(actor_id=actor.id, action="document_added", target=f"document:{document.id}",
                         details={"owner": f"{owner_type}:{owner_id}", "type": doc_type}))
    await session.commit()
    return document


async def get_document(session: AsyncSession, user: User, document_id: int) -> Document:
    document = await session.get(Document, document_id)
    if document is None or not await can_access(session, user, document.owner_type, document.owner_id):
        raise DocumentError("docs.not_found")
    return document


async def delete_document(session: AsyncSession, actor: User, document_id: int) -> None:
    document = await get_document(session, actor, document_id)
    if document.uploaded_by != actor.id and not await has_permission(actor, Permission.MANAGE_SETTINGS):
        raise DocumentError("access.no_permission")
    session.add(AuditLog(actor_id=actor.id, action="document_deleted", target=f"document:{document.id}",
                         details={"title": document.title}))
    await session.delete(document)
    await session.commit()


async def _visible(session: AsyncSession, user: User) -> Select:
    """Foydalanuvchi ko'ra oladigan hujjatlar so'rovi (ro'yxat va "muddati yaqin" uchun)."""
    stmt = select(Document)
    if user.role == UserRole.DRIVER:
        driver_alerts = select(Alert.id).where(Alert.driver_id == user.driver_id)
        return stmt.where(
            ((Document.owner_type == DocumentOwner.DRIVER.value) & (Document.owner_id == user.driver_id))
            | ((Document.owner_type == DocumentOwner.ALERT.value) & Document.owner_id.in_(driver_alerts))
        ) if user.driver_id else stmt.where(Document.id.is_(None))
    if await depot_scope(user) is None:
        return stmt
    vehicles = (await scoped_vehicles(user)).with_only_columns(Vehicle.id)
    drivers = (await scoped_drivers(user)).with_only_columns(Driver.id)
    return stmt.where(
        (Document.owner_type == DocumentOwner.COMPANY.value)
        | ((Document.owner_type == DocumentOwner.VEHICLE.value) & Document.owner_id.in_(vehicles))
        | ((Document.owner_type == DocumentOwner.DRIVER.value) & Document.owner_id.in_(drivers))
    )


async def list_documents(
    session: AsyncSession, user: User, owner_type: str | None, owner_id: int | None, page: int
) -> tuple[list[Document], int]:
    stmt = await _visible(session, user)
    if owner_type:
        stmt = stmt.where(Document.owner_type == owner_type)
    if owner_id:
        stmt = stmt.where(Document.owner_id == owner_id)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 0), pages - 1)
    documents = await session.scalars(stmt.order_by(Document.created_at.desc(), Document.id.desc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE))
    return list(documents), pages


async def expiring_documents(session: AsyncSession, user: User, today: date, within_days: int = 30) -> list[Document]:
    stmt = (await _visible(session, user)).where(
        Document.expires_at.is_not(None), Document.expires_at <= today + timedelta(days=within_days)
    )
    return list((await session.scalars(stmt.order_by(Document.expires_at))).all())
