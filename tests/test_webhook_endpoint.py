import json
import os
import time

import httpx
from sqlalchemy import select

from app.db.models import Alert, AlertDelivery
from app.main import app
from app.services.samsara.signature import compute_signature
from tests.test_webhook_parsing import incident_payload

SECRET = os.environ["SAMSARA_WEBHOOK_SECRET"]


class RecordingSender:
    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue(self, ids: list[int]) -> None:
        self.enqueued.extend(ids)


def _signed(body: bytes, secret: str = SECRET) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "X-Samsara-Timestamp": timestamp,
        "X-Samsara-Signature": compute_signature(secret, timestamp, body),
        "Content-Type": "application/json",
    }


async def _post(body: bytes, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/webhook/samsara", content=body, headers=headers)


async def test_signed_alert_is_stored_and_queued(session):
    app.state.alert_sender = sender = RecordingSender()
    body = json.dumps(incident_payload()).encode()

    response = await _post(body, _signed(body))

    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    deliveries = (await session.scalars(select(AlertDelivery))).all()
    assert sender.enqueued == [d.id for d in deliveries] and len(deliveries) == 1


async def test_retry_from_samsara_is_deduplicated(session):
    app.state.alert_sender = sender = RecordingSender()
    body = json.dumps(incident_payload()).encode()

    await _post(body, _signed(body))
    response = await _post(body, _signed(body))

    assert response.json()["duplicate"] is True
    assert len(sender.enqueued) == 1
    assert len((await session.scalars(select(Alert))).all()) == 1


async def test_bad_signature_is_rejected_and_nothing_stored(session):
    app.state.alert_sender = RecordingSender()
    body = json.dumps(incident_payload()).encode()
    headers = _signed(body)
    headers["X-Samsara-Signature"] = "v1=" + "0" * 64

    response = await _post(body, headers)

    assert response.status_code == 401
    assert (await session.scalars(select(Alert))).all() == []


async def test_missing_signature_is_rejected(session):
    response = await _post(b"{}", {"Content-Type": "application/json"})
    assert response.status_code == 401


async def test_invalid_payload_returns_422(session):
    body = b'{"eventType": "AlertIncident"}'
    assert (await _post(body, _signed(body))).status_code == 422
    body = b"[1, 2]"
    assert (await _post(body, _signed(body))).status_code == 422


async def test_ping_event_is_acknowledged_but_ignored(session):
    app.state.alert_sender = sender = RecordingSender()
    body = json.dumps({"eventId": "p1", "eventType": "Ping"}).encode()

    response = await _post(body, _signed(body))

    assert response.json() == {"ok": True, "ignored": True}
    assert sender.enqueued == []
