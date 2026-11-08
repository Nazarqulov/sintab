from datetime import UTC, datetime
from types import SimpleNamespace

from app.db.models import Depot, Group, RoutingRule, User, UserRole, UserStatus, Vehicle
from app.services.samsara.parser import NormalizedAlert

OWNER_CHAT_ID = 1000


def normalized(event_id: str = "evt-1", alert_type: str = "crash", **overrides) -> NormalizedAlert:
    values = dict(
        event_id=event_id,
        alert_type=alert_type,
        description="Crash detected",
        happened_at=datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
        vehicle_samsara_id="veh-1",
        vehicle_name="Truck 1",
        driver_samsara_id="drv-1",
        driver_name="Aziz",
        latitude=41.3,
        longitude=69.2,
        address="Tashkent",
        url="https://cloud.samsara.com/x",
        raw={"eventId": event_id},
    )
    values.update(overrides)
    return NormalizedAlert(**values)


async def add_user(session, telegram_id: int, role: UserRole, status: UserStatus = UserStatus.ACTIVE, **kw) -> User:
    user = User(telegram_id=telegram_id, role=role, status=status, **kw)
    session.add(user)
    await session.commit()
    return user


async def add_group(session, chat_id: int, *, depot: Depot | None = None, topic_id: int | None = None) -> Group:
    group = Group(chat_id=chat_id, topic_id=topic_id, title=f"g{chat_id}", depot=depot)
    session.add(group)
    await session.commit()
    return group


async def add_rule(session, group: Group, **criteria) -> RoutingRule:
    rule = RoutingRule(group=group, **criteria)
    session.add(rule)
    await session.commit()
    return rule


async def add_vehicle(session, samsara_id: str = "veh-1", *, depot: Depot | None = None, tag: str | None = None) -> Vehicle:
    vehicle = Vehicle(samsara_id=samsara_id, name="Truck 1", depot=depot, tag=tag)
    session.add(vehicle)
    await session.commit()
    return vehicle


class FakeBot:
    """send_message/edit_message_text chaqiruvlarini yozib oladi; `errors` navbat bilan ko'tariladi."""

    def __init__(self, errors: list[Exception] | None = None) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.errors = list(errors or [])
        self._next_message_id = 500

    async def send_message(self, **kwargs):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(kwargs)
        self._next_message_id += 1
        return SimpleNamespace(message_id=self._next_message_id)

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)
        return True
