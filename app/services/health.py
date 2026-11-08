import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AlertDelivery, DeliveryStatus
from app.services.monitoring import last_webhook_at
from app.services.runtime import runtime
from app.services.samsara.client import SamsaraClient


@dataclass
class HealthReport:
    db_ok: bool
    db_ms: float | None
    samsara: str  # "ok" | "no_token" | "error: ..."
    samsara_ms: float | None
    last_webhook: datetime | None
    pending: int
    failed_24h: int
    deferred: int
    queue_size: int | None
    uptime: timedelta


async def collect_health(session: AsyncSession, client_factory=None) -> HealthReport:
    started = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        db_ok, db_ms = True, (time.perf_counter() - started) * 1000
    except Exception:
        db_ok, db_ms = False, None

    samsara, samsara_ms = "no_token", None
    if settings.samsara_api_token:
        started = time.perf_counter()
        factory = client_factory or (lambda: SamsaraClient(settings.samsara_api_token, settings.samsara_api_base_url, max_retries=0))
        try:
            async with factory() as client:
                await client.ping()
            samsara, samsara_ms = "ok", (time.perf_counter() - started) * 1000
        except Exception as error:
            samsara = f"error: {error}"[:200]

    now = datetime.now(UTC)

    async def count(*conditions) -> int:
        return await session.scalar(select(func.count(AlertDelivery.id)).where(*conditions)) or 0

    return HealthReport(
        db_ok=db_ok,
        db_ms=db_ms,
        samsara=samsara,
        samsara_ms=samsara_ms,
        last_webhook=await last_webhook_at(session) if db_ok else None,
        pending=await count(AlertDelivery.status == DeliveryStatus.PENDING) if db_ok else 0,
        failed_24h=await count(
            AlertDelivery.status == DeliveryStatus.FAILED, AlertDelivery.created_at >= now - timedelta(hours=24)
        ) if db_ok else 0,
        deferred=await count(AlertDelivery.status == DeliveryStatus.DEFERRED) if db_ok else 0,
        queue_size=runtime.sender.queue_size if runtime.sender else None,
        uptime=now - runtime.started_at,
    )
