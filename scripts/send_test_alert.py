"""Imzolangan namunaviy Samsara alertini webhook'ga yuboradi (Samsara token'isiz sinash uchun).

Ishlatish (konteyner ichida):
    docker compose exec app python scripts/send_test_alert.py --type crash
    docker compose exec app python scripts/send_test_alert.py --type speeding --vehicle "Isuzu 07"
"""

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services.samsara.signature import compute_signature  # noqa: E402

DESCRIPTIONS = {
    "crash": "Crash detected",
    "speeding": "Vehicle Speed above 75 mph",
    "harsh_braking": "Harsh Brake",
    "geofence_exit": "Vehicle exits geofence: Main Depot",
    "geofence_entry": "Vehicle enters geofence: Customer Site",
    "engine_fault": "Engine fault code P0420",
    "low_fuel": "Fuel level below 15%",
    "hos_violation": "HOS violation: Driving time exceeded",
    "device_disconnected": "Vehicle gateway unplugged",
    "idle": "Vehicle idling for more than 30 minutes",
}


def build_payload(alert_type: str, vehicle: str, driver: str) -> dict:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "eventId": f"test-{uuid.uuid4()}",
        "eventTime": now,
        "eventType": "AlertIncident",
        "orgId": 1,
        "webhookId": "test",
        "data": {
            "configurationId": "test-config",
            "happenedAtTime": now,
            "isResolved": False,
            "incidentUrl": "https://cloud.samsara.com/",
            "conditions": [
                {
                    "description": DESCRIPTIONS[alert_type],
                    "details": {
                        "vehicle": {"id": f"test-{vehicle}", "name": vehicle},
                        "driver": {"id": f"test-{driver}", "name": driver},
                        "location": {
                            "latitude": 41.311081,
                            "longitude": 69.240562,
                            "formattedAddress": "Amir Temur ko'chasi, Toshkent",
                        },
                    },
                }
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=sorted(DESCRIPTIONS), default="crash")
    parser.add_argument("--vehicle", default="Test Truck 01")
    parser.add_argument("--driver", default="Test Driver")
    parser.add_argument("--url", default=f"http://localhost:{settings.app_port}/webhook/samsara")
    args = parser.parse_args()

    if not settings.samsara_webhook_secret:
        sys.exit("SAMSARA_WEBHOOK_SECRET .env faylida yo'q")

    body = json.dumps(build_payload(args.type, args.vehicle, args.driver)).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Samsara-Timestamp": timestamp,
        "X-Samsara-Signature": compute_signature(settings.samsara_webhook_secret, timestamp, body),
    }
    response = httpx.post(args.url, content=body, headers=headers, timeout=10)
    print(response.status_code, response.text)


if __name__ == "__main__":
    main()
