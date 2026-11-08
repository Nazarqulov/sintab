from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    samsara_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    plate: Mapped[str | None] = mapped_column(String(32))
    tag: Mapped[str | None] = mapped_column(String(64), index=True)
    depot_id: Mapped[int | None] = mapped_column(ForeignKey("depots.id", ondelete="SET NULL"))
    last_location: Mapped[dict | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fuel_percent: Mapped[float | None] = mapped_column(Float)
    fuel_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    depot: Mapped["Depot"] = relationship(back_populates="vehicles")
