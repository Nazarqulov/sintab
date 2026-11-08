from enum import Enum

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.i18n import t
from app.db.models import Alert, AlertStatus
from app.services.alerts.formatter import RESOLUTION_REASONS, maps_url


class AlertActionType(str, Enum):
    ACK = "ack"
    ASSIGN = "assign"
    RESOLVE = "resolve"
    MUTE = "mute"
    BACK = "back"


class AlertAction(CallbackData, prefix="al"):
    action: AlertActionType
    alert_id: int


class AlertReason(CallbackData, prefix="ar"):
    reason: str
    alert_id: int


def alert_keyboard(alert: Alert, lang: str | None = None) -> InlineKeyboardMarkup | None:
    map_button = None
    if alert.latitude is not None and alert.longitude is not None:
        map_button = InlineKeyboardButton(text=t("alert.btn_map", lang), url=maps_url(alert.latitude, alert.longitude))
    mute_button = _button(t("alert.btn_mute", lang), AlertActionType.MUTE, alert.id) if alert.vehicle_id else None

    if alert.status == AlertStatus.NEW:
        rows = [
            [_button(t("alert.btn_ack", lang), AlertActionType.ACK, alert.id),
             _button(t("alert.btn_assign", lang), AlertActionType.ASSIGN, alert.id)],
            [b for b in (map_button, mute_button) if b],
        ]
    elif alert.status == AlertStatus.ACKNOWLEDGED:
        rows = [
            [_button(t("alert.btn_resolve", lang), AlertActionType.RESOLVE, alert.id),
             _button(t("alert.btn_assign", lang), AlertActionType.ASSIGN, alert.id)],
            [b for b in (map_button, mute_button) if b],
        ]
    else:
        rows = [[map_button]] if map_button else []

    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def reasons_keyboard(alert_id: int, lang: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=t(f"alert.reason.{reason}", lang),
            callback_data=AlertReason(reason=reason, alert_id=alert_id).pack(),
        )]
        for reason in RESOLUTION_REASONS
    ]
    rows.append([_button(t("common.back", lang), AlertActionType.BACK, alert_id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _button(text: str, action: AlertActionType, alert_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AlertAction(action=action, alert_id=alert_id).pack())
