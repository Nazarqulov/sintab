from collections.abc import AsyncGenerator
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(AsyncAttrs, DeclarativeBase):
    pass


def enum_values(enum_cls: type[Enum]) -> list[str]:
    """Bazaga Enum a'zo nomini (OWNER) emas, qiymatini (owner) yozish uchun."""
    return [member.value for member in enum_cls]


# SQLite (faqat testlarda) ulanishlarni turli event loop'lar orasida qayta ishlata olmaydi.
_engine_options = (
    {"poolclass": NullPool} if settings.database_url.startswith("sqlite") else {"pool_pre_ping": True}
)
engine = create_async_engine(settings.database_url, **_engine_options)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
