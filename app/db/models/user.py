from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.associations import user_depots
from app.db.session import Base, enum_values


class UserRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    MANAGER = "manager"
    VIEWER = "viewer"
    DRIVER = "driver"
    BLOCKED = "blocked"
    PENDING = "pending"


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    position: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", values_callable=enum_values), default=UserRole.PENDING
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status", values_callable=enum_values), default=UserStatus.PENDING
    )
    language: Mapped[str] = mapped_column(String(8), default="uz")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tashkent")
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    depots: Mapped[list["Depot"]] = relationship(secondary=user_depots, back_populates="users")
    permission_overrides: Mapped[list["PermissionOverride"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="PermissionOverride.user_id"
    )
