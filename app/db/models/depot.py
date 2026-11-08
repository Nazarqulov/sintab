from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.associations import user_depots
from app.db.session import Base


class Depot(Base):
    __tablename__ = "depots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # passive_deletes: depo o'chganda bog'liq yozuvlarni baza o'zi (ON DELETE) hal qiladi —
    # ORM ularni async rejimda yuklashga urinmaydi.
    users: Mapped[list["User"]] = relationship(secondary=user_depots, back_populates="depots", passive_deletes=True)
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="depot", passive_deletes=True)
    drivers: Mapped[list["Driver"]] = relationship(back_populates="depot", passive_deletes=True)
    groups: Mapped[list["Group"]] = relationship(back_populates="depot", passive_deletes=True)
