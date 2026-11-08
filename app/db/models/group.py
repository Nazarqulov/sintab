from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Group(Base):
    """Telegram guruh (yoki forum topic) — alertlar shu yerga yuboriladi."""

    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("chat_id", "topic_id", name="uq_group_chat_topic"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(255))
    depot_id: Mapped[int | None] = mapped_column(ForeignKey("depots.id", ondelete="SET NULL"))
    language: Mapped[str] = mapped_column(String(8), default="uz", server_default="uz")
    # Critical alert vaqtida qabul qilinmasa, shu guruhga eskalatsiya yuboriladi.
    is_escalation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    bound_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    depot: Mapped["Depot"] = relationship(back_populates="groups")
    routing_rules: Mapped[list["RoutingRule"]] = relationship(back_populates="group", passive_deletes=True)
