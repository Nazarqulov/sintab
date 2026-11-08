"""Ichki chat: kim kim bilan yozisha oladi, kontaktlar ro'yxati va yozishma logi.

Qoidalar: xodimlar bir-biri bilan erkin yozishadi; haydovchi faqat o'z deposiga xizmat qiladigan
xodim bilan (Viewer'dan tashqari), haydovchilar o'zaro yozishmaydi. Faqat faol foydalanuvchilar.
"""

from aiogram.types import Message
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, Driver, User, UserRole, UserStatus
from app.services.scope import depot_scope

ROLE_ORDER = [UserRole.OWNER, UserRole.ADMIN, UserRole.DISPATCHER, UserRole.MANAGER, UserRole.VIEWER, UserRole.DRIVER]
FILE_ATTRS = ("document", "video", "audio", "voice", "video_note", "animation", "sticker")


async def _driver_depot(session: AsyncSession, user: User) -> int | None:
    if user.driver_id is None:
        return None
    driver = await session.get(Driver, user.driver_id)
    return driver.depot_id if driver else None


async def can_chat(session: AsyncSession, sender: User, recipient: User) -> bool:
    if sender.id == recipient.id or sender.status != UserStatus.ACTIVE or recipient.status != UserStatus.ACTIVE:
        return False
    drivers = [u for u in (sender, recipient) if u.role == UserRole.DRIVER]
    if not drivers:
        return True
    if len(drivers) == 2:
        return False
    driver, staff = (sender, recipient) if sender.role == UserRole.DRIVER else (recipient, sender)
    if staff.role == UserRole.VIEWER:
        return False
    scope = await depot_scope(staff)
    if scope is None:
        return True
    # Deposi noma'lum haydovchi — har qanday xodimga yoza oladi, aks holda yordamsiz qoladi.
    depot_id = await _driver_depot(session, driver)
    return depot_id is None or depot_id in scope


async def contacts(session: AsyncSession, user: User, query: str | None = None) -> list[User]:
    """Yozish mumkin bo'lgan foydalanuvchilar: avval oxirgi suhbatdoshlar, keyin rol va ism bo'yicha."""
    stmt = select(User).where(User.status == UserStatus.ACTIVE, User.id != user.id)
    if query:
        pattern = f"%{query.strip()[:64]}%"
        stmt = stmt.where(or_(User.full_name.ilike(pattern), User.username.ilike(pattern), User.phone.ilike(pattern)))
    candidates = [u for u in (await session.scalars(stmt)).all() if await can_chat(session, user, u)]

    partner = case((ChatMessage.sender_id == user.id, ChatMessage.recipient_id), else_=ChatMessage.sender_id)
    recent = dict((await session.execute(
        select(partner, func.max(ChatMessage.created_at))
        .where(or_(ChatMessage.sender_id == user.id, ChatMessage.recipient_id == user.id))
        .group_by(partner)
    )).all())

    def key(u: User):
        last = recent.get(u.id)
        return (last is None, -last.timestamp() if last else 0, ROLE_ORDER.index(u.role), (u.full_name or "").lower())

    return sorted(candidates, key=key)


def file_info(message: Message) -> tuple[str | None, str | None]:
    if message.photo:
        return message.photo[-1].file_id, None
    for attr in FILE_ATTRS:
        item = getattr(message, attr)
        if item is not None:
            return item.file_id, getattr(item, "file_name", None)
    return None, None


async def log_message(
    session: AsyncSession, sender: User, recipient: User, message: Message, delivered_message_id: int
) -> ChatMessage:
    file_id, file_name = file_info(message)
    entry = ChatMessage(
        sender_id=sender.id,
        recipient_id=recipient.id,
        content_type=getattr(message.content_type, "value", str(message.content_type)),
        text=message.text or message.caption,
        file_id=file_id,
        file_name=file_name,
        delivered_message_id=delivered_message_id,
    )
    session.add(entry)
    await session.commit()
    return entry


async def find_reply_target(session: AsyncSession, user: User, replied_message_id: int) -> User | None:
    """Foydalanuvchi bot yetkazgan xabarga Telegram reply qilsa — asl yuboruvchi."""
    entry = await session.scalar(
        select(ChatMessage)
        .where(ChatMessage.recipient_id == user.id, ChatMessage.delivered_message_id == replied_message_id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    if entry is None or entry.sender_id is None:
        return None
    return await session.get(User, entry.sender_id)
