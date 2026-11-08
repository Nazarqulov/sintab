from collections.abc import Mapping
from enum import Enum


class AlertType(str, Enum):
    CRASH = "crash"
    PANIC_BUTTON = "panic_button"
    HARSH_BRAKING = "harsh_braking"
    HARSH_ACCELERATION = "harsh_acceleration"
    HARSH_TURN = "harsh_turn"
    SPEEDING = "speeding"
    HOS_VIOLATION = "hos_violation"
    ENGINE_FAULT = "engine_fault"
    LOW_FUEL = "low_fuel"
    MAINTENANCE_DUE = "maintenance_due"
    DEVICE_DISCONNECTED = "device_disconnected"
    IDLE = "idle"
    GEOFENCE_ENTRY = "geofence_entry"
    GEOFENCE_EXIT = "geofence_exit"
    OTHER = "other"


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


DEFAULT_SEVERITY: dict[AlertType, Severity] = {
    AlertType.CRASH: Severity.CRITICAL,
    AlertType.PANIC_BUTTON: Severity.CRITICAL,
    AlertType.HOS_VIOLATION: Severity.CRITICAL,
    AlertType.ENGINE_FAULT: Severity.CRITICAL,
    AlertType.HARSH_BRAKING: Severity.WARNING,
    AlertType.HARSH_ACCELERATION: Severity.WARNING,
    AlertType.HARSH_TURN: Severity.WARNING,
    AlertType.SPEEDING: Severity.WARNING,
    AlertType.LOW_FUEL: Severity.WARNING,
    AlertType.DEVICE_DISCONNECTED: Severity.WARNING,
    AlertType.GEOFENCE_EXIT: Severity.WARNING,
    AlertType.GEOFENCE_ENTRY: Severity.INFO,
    AlertType.IDLE: Severity.INFO,
    AlertType.MAINTENANCE_DUE: Severity.INFO,
    AlertType.OTHER: Severity.INFO,
}

# app_settings jadvalidagi kalit: {"speeding": "critical", ...} — admin standartni o'zgartirishi uchun.
SEVERITY_OVERRIDES_SETTING = "severity_overrides"


def resolve_severity(alert_type: str, resolved: bool, overrides: Mapping[str, str] | None = None) -> Severity:
    if resolved:
        return Severity.INFO
    override = (overrides or {}).get(alert_type)
    if override in {s.value for s in Severity}:
        return Severity(override)
    try:
        return DEFAULT_SEVERITY[AlertType(alert_type)]
    except ValueError:
        return Severity.INFO
