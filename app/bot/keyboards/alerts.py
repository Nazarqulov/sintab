from enum import Enum

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import Alert, AlertStatus
from app.services.alerts.formatter import maps_url


class AlertActionType(str, Enum):
    ACK = "ack"
    ASSIGN = "assign"
    RESOLVE = "resolve"
    MUTE = "mute"


class AlertAction(CallbackData, prefix="al"):
    action: AlertActionType
    alert_id: int


def alert_keyboard(alert: Alert) -> InlineKeyboardMarkup | None:
    map_button = None
    if alert.latitude is not None and alert.longitude is not None:
        map_button = InlineKeyboardButton(text="🗺 Xaritada ko'rish", url=maps_url(alert.latitude, alert.longitude))
    mute_button = _button("🔕 Mute 1 soat", AlertActionType.MUTE, alert.id) if alert.vehicle_id else None

    if alert.status == AlertStatus.NEW:
        rows = [
            [_button("✅ Qabul qildim", AlertActionType.ACK, alert.id),
             _button("👤 Menga biriktir", AlertActionType.ASSIGN, alert.id)],
            [b for b in (map_button, mute_button) if b],
        ]
    elif alert.status == AlertStatus.ACKNOWLEDGED:
        rows = [
            [_button("✔️ Yopish", AlertActionType.RESOLVE, alert.id),
             _button("👤 Menga biriktir", AlertActionType.ASSIGN, alert.id)],
            [b for b in (map_button, mute_button) if b],
        ]
    else:
        rows = [[map_button]] if map_button else []

    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _button(text: str, action: AlertActionType, alert_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AlertAction(action=action, alert_id=alert_id).pack())
