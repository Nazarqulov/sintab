from collections.abc import AsyncGenerator
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def enum_values(enum_cls: type[Enum]) -> list[str]:
    """Bazaga Enum a'zo nomini (OWNER) emas, qiymatini (owner) yozish uchun."""
    return [member.value for member in enum_cls]


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
