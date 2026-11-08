from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base, enum_values


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    # Tungi past darajali alert — ertalabki digest'ga qoldirilgan / digest bilan yuborilgan.
    DEFERRED = "deferred"
    DIGESTED = "digested"


class DeliveryKind(str, Enum):
    ROUTE = "route"            # routing qoidasi bo'yicha guruh
    OWNER = "owner"            # qoida topilmadi — Owner'ga
    PERSONAL = "personal"      # foydalanuvchining shaxsiy bildirishnomasi
    ESCALATION = "escalation"  # qabul qilinmagan critical alert


class AlertDelivery(Base):
    """Bitta alertni bitta chat/topic'ga yuborish. Bazada turgani uchun restart'da yo'qolmaydi."""

    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    # NULL — routing qoidasi topilmagani uchun Owner'ning shaxsiy chatiga yuborilgan.
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), default=DeliveryKind.ROUTE.value, server_default=DeliveryKind.ROUTE.value)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    topic_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[DeliveryStatus] = mapped_column(
        SAEnum(DeliveryStatus, name="delivery_status", values_callable=enum_values),
        default=DeliveryStatus.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    alert: Mapped["Alert"] = relationship(back_populates="deliveries")
