from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.services.alerts.types import AlertType, Severity, resolve_severity
from app.services.samsara.parser import classify, parse_webhook


def incident_payload(description: str = "Vehicle Speed above 75 mph", **data_overrides) -> dict:
    data = {
        "configurationId": "cfg-1",
        "happenedAtTime": "2026-11-08T10:15:00Z",
        "isResolved": False,
        "incidentUrl": "https://cloud.samsara.com/o/1/alerts/incidents/abc",
        "conditions": [
            {
                "triggerId": 1000,
                "description": description,
                "details": {
                    "speed": {
                        "vehicle": {"id": "281474", "name": "Truck 12", "externalIds": {}},
                        "driver": {"id": "55", "name": "Aziz Karimov"},
                        "location": {"latitude": 41.3, "longitude": 69.24, "formattedAddress": "Tashkent"},
                    }
                },
            }
        ],
    }
    data.update(data_overrides)
    return {"eventId": "evt-1", "eventTime": "2026-11-08T10:15:05Z", "eventType": "AlertIncident", "data": data}


def test_alert_incident_is_normalized():
    alert = parse_webhook(incident_payload())

    assert alert.event_id == "evt-1"
    assert alert.alert_type == AlertType.SPEEDING
    assert alert.vehicle_samsara_id == "281474"
    assert alert.vehicle_name == "Truck 12"
    assert alert.driver_samsara_id == "55"
    assert alert.driver_name == "Aziz Karimov"
    assert (alert.latitude, alert.longitude, alert.address) == (41.3, 69.24, "Tashkent")
    assert alert.happened_at == datetime(2026, 11, 8, 10, 15, tzinfo=UTC)
    assert alert.url.endswith("/abc")
    assert alert.resolved is False
    assert alert.raw["eventId"] == "evt-1"


def test_resolved_incident_is_flagged():
    assert parse_webhook(incident_payload(isResolved=True)).resolved is True


def test_reverse_geo_address_is_used():
    payload = incident_payload()
    payload["data"]["conditions"][0]["details"]["speed"]["location"] = {
        "latitude": 1.5,
        "longitude": 2.5,
        "reverseGeo": {"formattedLocation": "Samarkand"},
    }
    assert parse_webhook(payload).address == "Samarkand"


def test_incident_without_vehicle_or_location_still_parses():
    payload = incident_payload()
    payload["data"]["conditions"][0]["details"] = {}
    alert = parse_webhook(payload)
    assert alert.vehicle_samsara_id is None
    assert alert.latitude is None


def test_legacy_alert_format_is_normalized():
    payload = {
        "eventId": "legacy-1",
        "eventMs": 1762596900000,
        "eventType": "Alert",
        "event": {
            "alertConditionDescription": "Harsh Brake",
            "summary": "Truck 3 braked hard",
            "device": {"id": 99, "name": "Truck 3"},
            "startMs": 1762596900000,
            "resolved": False,
            "alertEventUrl": "https://cloud.samsara.com/x",
        },
    }
    alert = parse_webhook(payload)
    assert alert.alert_type == AlertType.HARSH_BRAKING
    assert alert.vehicle_samsara_id == "99"
    assert alert.description == "Harsh Brake — Truck 3 braked hard"
    assert alert.happened_at.year == 2025


def test_non_alert_events_are_ignored():
    assert parse_webhook({"eventId": "p", "eventType": "Ping"}) is None


def test_missing_event_id_is_rejected():
    with pytest.raises(ValidationError):
        parse_webhook({"eventType": "AlertIncident", "data": {}})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Crash detected", AlertType.CRASH),
        ("Harsh Brake", AlertType.HARSH_BRAKING),
        ("Hard acceleration event", AlertType.HARSH_ACCELERATION),
        ("Vehicle Speed above 75 mph", AlertType.SPEEDING),
        ("Speeding in geofence", AlertType.SPEEDING),
        ("HOS violation", AlertType.HOS_VIOLATION),
        ("Engine fault code P0420", AlertType.ENGINE_FAULT),
        ("Fuel level below 15%", AlertType.LOW_FUEL),
        ("Scheduled maintenance due", AlertType.MAINTENANCE_DUE),
        ("Vehicle gateway unplugged", AlertType.DEVICE_DISCONNECTED),
        ("Vehicle idling for 30 min", AlertType.IDLE),
        ("Vehicle exits geofence: Depot", AlertType.GEOFENCE_EXIT),
        ("Vehicle enters geofence: Depot", AlertType.GEOFENCE_ENTRY),
        ("Panic button pressed", AlertType.PANIC_BUTTON),
        ("Data center maintenance window", AlertType.MAINTENANCE_DUE),
        ("Something unusual", AlertType.OTHER),
    ],
)
def test_classify(text, expected):
    assert classify(text) == expected


def test_severity_defaults_and_overrides():
    assert resolve_severity("crash", resolved=False) == Severity.CRITICAL
    assert resolve_severity("speeding", resolved=False) == Severity.WARNING
    assert resolve_severity("idle", resolved=False) == Severity.INFO
    assert resolve_severity("crash", resolved=True) == Severity.INFO
    assert resolve_severity("speeding", resolved=False, overrides={"speeding": "critical"}) == Severity.CRITICAL
    assert resolve_severity("speeding", resolved=False, overrides={"speeding": "bogus"}) == Severity.WARNING
    assert resolve_severity("unknown-type", resolved=False) == Severity.INFO
