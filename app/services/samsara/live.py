"""/fleet/vehicles/stats javobidan mashinaning jonli holati (gps, dvigatel, yoqilg'i)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.samsara.parser import _parse_time


@dataclass
class LiveStatus:
    latitude: float | None = None
    longitude: float | None = None
    speed_mph: float | None = None
    heading: float | None = None
    address: str | None = None
    gps_time: datetime | None = None
    engine: str | None = None
    fuel_percent: float | None = None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_vehicle_stats(item: dict[str, Any]) -> LiveStatus:
    gps = item.get("gps") if isinstance(item.get("gps"), dict) else {}
    engine = item.get("engineState") if isinstance(item.get("engineState"), dict) else {}
    fuel = item.get("fuelPercent") if isinstance(item.get("fuelPercent"), dict) else {}
    reverse_geo = gps.get("reverseGeo") if isinstance(gps.get("reverseGeo"), dict) else {}
    return LiveStatus(
        latitude=_number(gps.get("latitude")),
        longitude=_number(gps.get("longitude")),
        speed_mph=_number(gps.get("speedMilesPerHour")),
        heading=_number(gps.get("headingDegrees")),
        address=reverse_geo.get("formattedLocation") if isinstance(reverse_geo.get("formattedLocation"), str) else None,
        gps_time=_parse_time(gps["time"]) if gps.get("time") else None,
        engine=engine.get("value") if isinstance(engine.get("value"), str) else None,
        fuel_percent=_number(fuel.get("value")),
    )
