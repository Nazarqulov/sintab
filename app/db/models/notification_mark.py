from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class NotificationMark(Base):
    """Proaktiv ogohlantirish yuborilganini eslab qolish (HOS, hujjat muddati, yoqilg'i...) — restart'da takrorlanmasin."""

    __tablename__ = "notification_marks"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
