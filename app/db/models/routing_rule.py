from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class RoutingRule(Base):
    """alert_type/severity/depot_id/vehicle_tag = NULL demak 'har qanday' (wildcard).

    Bir nechta qoida mos kelsa, `priority` yuqori bo'lgani tanlanadi.
    """

    __tablename__ = "routing_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_type: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str | None] = mapped_column(String(16))
    depot_id: Mapped[int | None] = mapped_column(ForeignKey("depots.id", ondelete="SET NULL"))
    vehicle_tag: Mapped[str | None] = mapped_column(String(64))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped["Group"] = relationship(back_populates="routing_rules")
