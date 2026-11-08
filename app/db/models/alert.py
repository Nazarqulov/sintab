from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, false, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base, enum_values


class AlertStatus(str, Enum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    samsara_alert_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    alert_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id", ondelete="SET NULL"))
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    happened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    address: Mapped[str | None] = mapped_column(String(255))
    samsara_url: Mapped[str | None] = mapped_column(String(512))
    # Mute faol bo'lgani uchun saqlangan, lekin hech kimga yuborilmagan alert.
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus, name="alert_status", values_callable=enum_values), default=AlertStatus.NEW, index=True
    )
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Xabar matnini yasash uchun har doim kerak — async'da lazy-load ishlamagani uchun selectin.
    vehicle: Mapped["Vehicle | None"] = relationship(lazy="selectin")
    driver: Mapped["Driver | None"] = relationship(lazy="selectin")
    acknowledged_by_user: Mapped["User | None"] = relationship(foreign_keys=[acknowledged_by], lazy="selectin")
    assigned_to_user: Mapped["User | None"] = relationship(foreign_keys=[assigned_to], lazy="selectin")
    resolved_by_user: Mapped["User | None"] = relationship(foreign_keys=[resolved_by], lazy="selectin")
    deliveries: Mapped[list["AlertDelivery"]] = relationship(back_populates="alert", cascade="all, delete-orphan")
