"""Telegram Mini App `initData` imzosini tekshirish.

secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
hash       = hex(HMAC_SHA256(key=secret_key, msg=data_check_string)),
data_check_string — `hash`dan boshqa barcha maydonlar, kalit bo'yicha saralangan "k=v", "\n" bilan.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlencode

MAX_AGE_SECONDS = 24 * 3600


class InitDataError(ValueError):
    pass


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Testlar va lokal sinov uchun: maydonlardan imzolangan initData satri."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    digest = hmac.new(_secret_key(bot_token), check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = MAX_AGE_SECONDS, now: float | None = None) -> dict:
    """Imzo va muddat to'g'ri bo'lsa — Telegram foydalanuvchisi (dict); aks holda InitDataError."""
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as error:
        raise InitDataError("malformed") from error
    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    expected = hmac.new(_secret_key(bot_token), check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise InitDataError("bad signature")
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as error:
        raise InitDataError("bad auth_date") from error
    if (now if now is not None else time.time()) - auth_date > max_age:
        raise InitDataError("expired")
    try:
        user = json.loads(fields.get("user", ""))
    except json.JSONDecodeError as error:
        raise InitDataError("no user") from error
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise InitDataError("no user")
    return user
