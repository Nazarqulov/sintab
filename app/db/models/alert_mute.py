from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AlertMute(Base):
    """Ma'lum mashinaning ma'lum turdagi alertlarini vaqtincha yubormaslik ("🔕 Mute 1 soat")."""

    __tablename__ = "alert_mutes"
    __table_args__ = (Index("ix_alert_mutes_vehicle_type", "vehicle_id", "alert_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(String(64))
    muted_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    muted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
