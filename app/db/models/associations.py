from sqlalchemy import Column, ForeignKey, Table

from app.db.session import Base

user_depots = Table(
    "user_depots",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("depot_id", ForeignKey("depots.id", ondelete="CASCADE"), primary_key=True),
)
