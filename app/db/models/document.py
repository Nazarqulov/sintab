from datetime import date, datetime
from enum import Enum

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class DocumentOwner(str, Enum):
    DRIVER = "driver"
    VEHICLE = "vehicle"
    ALERT = "alert"
    COMPANY = "company"


class DocumentType(str, Enum):
    CDL = "cdl"
    MEDICAL_CARD = "medical_card"
    INSURANCE = "insurance"
    REGISTRATION = "registration"
    IFTA = "ifta"
    ANNUAL_INSPECTION = "annual_inspection"
    BOL = "bol"
    RATE_CONFIRMATION = "rate_confirmation"
    POD = "pod"
    REPAIR = "repair"
    ACCIDENT_REPORT = "accident_report"
    REPORT = "report"
    OTHER = "other"


class Document(Base):
    """Fayl Telegram serverida qoladi — bazada faqat file_id (bot uni istalgan vaqt qayta yubora oladi)."""

    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_owner", "owner_type", "owner_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[int | None] = mapped_column(Integer)
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[str] = mapped_column(String(255))
    file_unique_id: Mapped[str] = mapped_column(String(64))
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    # Muddatli hujjatlar (CDL, medical card, sug'urta...) uchun — eslatmalar shu sanaga qarab.
    expires_at: Mapped[date | None] = mapped_column(Date, index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    uploader: Mapped["User | None"] = relationship(lazy="selectin")
