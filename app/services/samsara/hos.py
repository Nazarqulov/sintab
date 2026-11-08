"""/fleet/hos/clocks javobidan haydovchining HOS soatlari (qolgan haydash/smena/sikl vaqti)."""

from dataclasses import dataclass
from typing import Any


@dataclass
class HosClock:
    driver_samsara_id: str
    driver_name: str | None
    duty_status: str | None
    vehicle_name: str | None
    drive_remaining_ms: int | None
    shift_remaining_ms: int | None
    cycle_remaining_ms: int | None
    break_remaining_ms: int | None

    @property
    def tightest_ms(self) -> int | None:
        """Haydovchini birinchi to'xtatadigan cheklov (haydash yoki smena)."""
        values = [v for v in (self.drive_remaining_ms, self.shift_remaining_ms) if v is not None]
        return min(values) if values else None

    @property
    def is_working(self) -> bool:
        return (self.duty_status or "").lower() in {"driving", "ondutynotdriving", "on_duty", "onduty", "yardmove", "yard_move"}


def _ms(container: Any, key: str) -> int | None:
    if not isinstance(container, dict):
        return None
    value = container.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_hos_clock(item: dict[str, Any]) -> HosClock | None:
    driver = item.get("driver") if isinstance(item.get("driver"), dict) else {}
    driver_id = driver.get("id")
    if driver_id is None:
        return None
    clocks = item.get("clocks") if isinstance(item.get("clocks"), dict) else {}
    duty = item.get("currentDutyStatus") if isinstance(item.get("currentDutyStatus"), dict) else {}
    vehicle = item.get("currentVehicle") if isinstance(item.get("currentVehicle"), dict) else {}
    return HosClock(
        driver_samsara_id=str(driver_id),
        driver_name=driver.get("name") if isinstance(driver.get("name"), str) else None,
        duty_status=duty.get("hosStatusType") if isinstance(duty.get("hosStatusType"), str) else None,
        vehicle_name=vehicle.get("name") if isinstance(vehicle.get("name"), str) else None,
        drive_remaining_ms=_ms(clocks.get("drive"), "driveRemainingDurationMs"),
        shift_remaining_ms=_ms(clocks.get("shift"), "shiftRemainingDurationMs"),
        cycle_remaining_ms=_ms(clocks.get("cycle"), "cycleRemainingDurationMs"),
        break_remaining_ms=_ms(clocks.get("break"), "timeUntilBreakDurationMs"),
    )


def format_duration(ms: int | None) -> str:
    if ms is None:
        return "—"
    minutes = max(ms // 60000, 0)
    return f"{minutes // 60}h {minutes % 60:02d}m"
