import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Depot, User, UserRole, UserStatus

logger = logging.getLogger(__name__)

SEND_INTERVAL = 0.05  # ~20 xabar/soniya — Telegram'ning umumiy limitidan past


@dataclass
class BroadcastResult:
    sent: int
    failed: int


async def audience(session: AsyncSession, target: str) -> list[User]:
    """target: "all" | "role-<role>" | "depot-<id>" (":" — callback ajratuvchisi, ishlatib bo'lmaydi)."""
    stmt = select(User).where(User.status == UserStatus.ACTIVE)
    if target.startswith("role-"):
        stmt = stmt.where(User.role == UserRole(target.removeprefix("role-")))
    elif target.startswith("depot-"):
        depot_id = int(target.removeprefix("depot-"))
        stmt = stmt.where(User.depots.any(Depot.id == depot_id))
    elif target != "all":
        raise ValueError(target)
    return list((await session.scalars(stmt.order_by(User.id))).all())


async def send_broadcast(
    session: AsyncSession,
    bot: Bot,
    actor: User,
    recipients: list[User],
    from_chat_id: int,
    message_id: int,
    *,
    sleep=asyncio.sleep,
) -> BroadcastResult:
    """Admin yuborgan xabarni (matn, rasm, PDF, Excel...) copy_message bilan har bir oluvchiga nusxalaydi."""
    sent = failed = 0
    for user in recipients:
        if user.id == actor.id:
            continue
        for _ in range(3):
            try:
                await bot.copy_message(chat_id=user.telegram_id, from_chat_id=from_chat_id, message_id=message_id)
                sent += 1
                break
            except TelegramRetryAfter as error:
                await sleep(error.retry_after)
            except TelegramAPIError as error:
                logger.info("Broadcast %s ga yetmadi: %s", user.telegram_id, error)
                failed += 1
                break
        else:
            failed += 1
        await sleep(SEND_INTERVAL)
    session.add(AuditLog(actor_id=actor.id, action="broadcast", details={"sent": sent, "failed": failed}))
    await session.commit()
    return BroadcastResult(sent, failed)
