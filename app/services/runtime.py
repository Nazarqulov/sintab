"""Jarayon darajasidagi holat: /status va monitoring uchun (handler'lar FastAPI app'iga kira olmaydi)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.alerts.sender import AlertSender
    from app.services.monitoring import OwnerNotifier


@dataclass
class RuntimeState:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sender: "AlertSender | None" = None
    notifier: "OwnerNotifier | None" = None


runtime = RuntimeState()
