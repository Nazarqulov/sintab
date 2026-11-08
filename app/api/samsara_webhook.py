import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.alerts.ingest import ingest_alert
from app.services.samsara.parser import parse_webhook
from app.services.samsara.signature import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY_BYTES = 1_000_000


@router.post("/webhook/samsara")
async def samsara_webhook(
    request: Request,
    x_samsara_signature: str | None = Header(default=None),
    x_samsara_timestamp: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    if not settings.samsara_webhook_secret:
        logger.error("SAMSARA_WEBHOOK_SECRET sozlanmagan — webhook rad etildi")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not verify_signature(settings.samsara_webhook_secret, x_samsara_timestamp, body, x_samsara_signature):
        logger.warning("Samsara webhook: imzo noto'g'ri yoki eskirgan (ip=%s)", request.client.host if request.client else "?")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload obyekt emas")
        alert = parse_webhook(payload)
    except (ValueError, ValidationError) as error:
        logger.warning("Samsara webhook: payload noto'g'ri: %s", error)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from error

    if alert is None:
        return {"ok": True, "ignored": True}

    async with AsyncSessionLocal() as session:
        result = await ingest_alert(session, alert, owner_chat_id=settings.owner_telegram_id)

    if result.delivery_ids:
        request.app.state.alert_sender.enqueue(result.delivery_ids)
    return {"ok": True, "alert_id": result.alert_id, "duplicate": not result.created, "suppressed": result.suppressed}
