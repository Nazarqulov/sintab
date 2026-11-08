"""Samsara webhook payload'ini yagona NormalizedAlert ko'rinishiga keltirish.

Ikki format qo'llanadi: Webhooks 2.0 "AlertIncident" va eski (1.0) "Alert".
Samsara alert sozlamasiga qarab `details` tuzilishi har xil, shuning uchun mashina,
haydovchi va joylashuv rekursiv qidiriladi. Asl payload har doim alerts.payload'da saqlanadi.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.alerts.types import AlertType

MAX_SEARCH_DEPTH = 8


class SamsaraEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    eventId: str = Field(min_length=1, max_length=128)
    eventType: str = Field(min_length=1, max_length=64)


@dataclass
class NormalizedAlert:
    event_id: str
    alert_type: str
    description: str
    happened_at: datetime
    resolved: bool = False
    vehicle_samsara_id: str | None = None
    vehicle_name: str | None = None
    driver_samsara_id: str | None = None
    driver_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None
    url: str | None = None
    raw: dict = field(default_factory=dict)


# Tartib muhim: aniqroq naqshlar umumiyroqlaridan oldin (masalan "harsh brake" "speed"dan oldin).
_CLASSIFIERS: list[tuple[AlertType, re.Pattern[str]]] = [
    (AlertType.CRASH, re.compile(r"crash|collision")),
    (AlertType.PANIC_BUTTON, re.compile(r"panic")),
    (AlertType.HARSH_BRAKING, re.compile(r"(harsh|hard)\s*brak")),
    (AlertType.HARSH_ACCELERATION, re.compile(r"(harsh|hard)\s*accel")),
    (AlertType.HARSH_TURN, re.compile(r"(harsh|sharp|hard)\s*turn")),
    (AlertType.SPEEDING, re.compile(r"speed")),
    (AlertType.HOS_VIOLATION, re.compile(r"\bhos\b|hours of service|duty status|driving time")),
    (AlertType.ENGINE_FAULT, re.compile(r"fault|check engine|engine light|\bdtc\b")),
    (AlertType.LOW_FUEL, re.compile(r"fuel")),
    (AlertType.MAINTENANCE_DUE, re.compile(r"maintenance|service due|dvir")),
    (AlertType.DEVICE_DISCONNECTED, re.compile(r"disconnect|unplug|offline|lost connection|tamper")),
    (AlertType.IDLE, re.compile(r"idl")),
    (AlertType.GEOFENCE_EXIT, re.compile(r"(geofence|address).*(exit|leav|left|outside)|(exit|leav|left).*(geofence|address)")),
    (AlertType.GEOFENCE_ENTRY, re.compile(r"geofence|\baddress\b|arriv|\benter|\binside")),
]


def classify(text: str) -> AlertType:
    lowered = text.lower()
    for alert_type, pattern in _CLASSIFIERS:
        if pattern.search(lowered):
            return alert_type
    return AlertType.OTHER


def parse_webhook(payload: dict[str, Any]) -> NormalizedAlert | None:
    """Alert bo'lmagan hodisalar (masalan Ping) uchun None. Noto'g'ri konvert — ValidationError."""
    envelope = SamsaraEnvelope.model_validate(payload)
    if envelope.eventType == "AlertIncident":
        return _parse_incident(envelope.eventId, payload)
    if envelope.eventType == "Alert":
        return _parse_legacy(envelope.eventId, payload)
    return None


def _parse_incident(event_id: str, payload: dict[str, Any]) -> NormalizedAlert:
    data = _as_dict(payload.get("data"))
    conditions = data.get("conditions") if isinstance(data.get("conditions"), list) else []
    condition = _as_dict(conditions[0]) if conditions else {}
    details = condition.get("details") or {}

    description = _clean(condition.get("description")) or _clean(data.get("configurationName")) or "Samsara alert"
    vehicle = _find_dict(details, "vehicle") or _find_dict(data, "vehicle") or {}
    driver = _find_dict(details, "driver") or _find_dict(data, "driver") or {}
    location = _find_location(details) or _find_location(data)

    return _build(
        event_id=event_id,
        description=description,
        happened_at=_parse_time(data.get("happenedAtTime") or payload.get("eventTime")),
        resolved=bool(data.get("isResolved")),
        vehicle=vehicle,
        driver=driver,
        location=location,
        url=_clean(data.get("incidentUrl")),
        raw=payload,
    )


def _parse_legacy(event_id: str, payload: dict[str, Any]) -> NormalizedAlert:
    event = _as_dict(payload.get("event"))
    description = _clean(event.get("alertConditionDescription")) or "Samsara alert"
    summary = _clean(event.get("summary"))
    if summary and summary != description:
        description = f"{description} — {summary}"

    return _build(
        event_id=event_id,
        description=description,
        happened_at=_parse_time(event.get("startMs") or payload.get("eventMs")),
        resolved=bool(event.get("resolved")),
        vehicle=_as_dict(event.get("device")),
        driver=_as_dict(event.get("driver")),
        location=_find_location(event),
        url=_clean(event.get("alertEventUrl")),
        raw=payload,
    )


def _build(
    *,
    event_id: str,
    description: str,
    happened_at: datetime,
    resolved: bool,
    vehicle: dict,
    driver: dict,
    location: tuple[float, float, str | None] | None,
    url: str | None,
    raw: dict,
) -> NormalizedAlert:
    latitude, longitude, address = location if location else (None, None, None)
    return NormalizedAlert(
        event_id=event_id,
        alert_type=classify(description).value,
        description=description,
        happened_at=happened_at,
        resolved=resolved,
        vehicle_samsara_id=_ident(vehicle),
        vehicle_name=_clean(vehicle.get("name")),
        driver_samsara_id=_ident(driver),
        driver_name=_clean(driver.get("name")),
        latitude=latitude,
        longitude=longitude,
        address=address,
        url=url,
        raw=raw,
    )


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _ident(obj: dict) -> str | None:
    value = obj.get("id")
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
        return str(value)
    return None


def _find_dict(obj: Any, key: str, depth: int = 0) -> dict | None:
    if depth > MAX_SEARCH_DEPTH:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get(key), dict):
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_dict(child, key, depth + 1)
        if found is not None:
            return found
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _find_location(obj: Any, depth: int = 0) -> tuple[float, float, str | None] | None:
    if depth > MAX_SEARCH_DEPTH:
        return None
    if isinstance(obj, dict):
        latitude, longitude = obj.get("latitude"), obj.get("longitude")
        if _is_number(latitude) and _is_number(longitude):
            return float(latitude), float(longitude), _address_from(obj)
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_location(child, depth + 1)
        if found is not None:
            return found
    return None


def _address_from(obj: dict) -> str | None:
    for key in ("formattedAddress", "formattedLocation", "address"):
        value = _clean(obj.get(key))
        if value:
            return value[:255]
    reverse_geo = _as_dict(obj.get("reverseGeo"))
    value = _clean(reverse_geo.get("formattedLocation"))
    return value[:255] if value else None


def _parse_time(value: Any) -> datetime:
    if _is_number(value):
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
