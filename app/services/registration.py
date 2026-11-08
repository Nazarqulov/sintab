import re

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.db.models import AuditLog, Depot, Driver, User, UserRole, UserStatus
from app.services.users import get_user_by_telegram_id, has_permission

SUPPORTED_LANGUAGES = {"uz", "ru", "en"}
PHONE_MATCH_DIGITS = 10  # AQSh raqami: mamlakat kodisiz oxirgi 10 raqam


def detect_language(language_code: str | None) -> str:
    code = (language_code or "").split("-")[0].lower()
    return code if code in SUPPORTED_LANGUAGES else "uz"


async def get_or_create_user(session: AsyncSession, tg_user: TelegramUser) -> User:
    """Birinchi /start'da `pending` yozuv yaratadi; mavjud bo'lsa username'ni yangilaydi."""
    user = await get_user_by_telegram_id(session, tg_user.id)
    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            role=UserRole.PENDING,
            status=UserStatus.PENDING,
            language=detect_language(tg_user.language_code),
        )
        session.add(user)
    else:
        user.username = tg_user.username
    if not user.full_name and user.status == UserStatus.ACTIVE:
        user.full_name = tg_user.full_name
    await session.commit()
    return user


def is_submitted(user: User) -> bool:
    return user.status == UserStatus.PENDING and bool(user.phone)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"+{digits}" if digits else ""


async def match_driver_by_phone(session: AsyncSession, phone: str) -> Driver | None:
    tail = re.sub(r"\D", "", phone)[-PHONE_MATCH_DIGITS:]
    if len(tail) < 7:
        return None
    drivers = await session.scalars(select(Driver).where(Driver.phone.is_not(None)))
    matches = [d for d in drivers if re.sub(r"\D", "", d.phone)[-PHONE_MATCH_DIGITS:] == tail]
    # Bir nechta haydovchi mos kelsa — taxmin qilmaymiz, admin qo'lda hal qiladi.
    return matches[0] if len(matches) == 1 else None


async def submit_registration(
    session: AsyncSession,
    user: User,
    *,
    full_name: str,
    phone: str,
    position: str,
    depot_id: int | None,
) -> User:
    user.full_name = full_name
    user.phone = normalize_phone(phone)
    user.position = position
    depot = await session.get(Depot, depot_id) if depot_id else None
    await user.awaitable_attrs.depots
    user.depots = [depot] if depot else []
    driver = await match_driver_by_phone(session, user.phone)
    user.driver_id = driver.id if driver else None
    session.add(AuditLog(actor_id=user.id, action="registration_submitted", target=f"user:{user.id}"))
    await session.commit()
    await session.refresh(user, ["driver"])
    return user


async def list_approvers(session: AsyncSession) -> list[User]:
    """Yangi so'rovlarni ko'ra oladiganlar: faol va manage_users huquqi borlar."""
    active = (await session.scalars(select(User).where(User.status == UserStatus.ACTIVE))).all()
    return [u for u in active if await has_permission(u, Permission.MANAGE_USERS)]
