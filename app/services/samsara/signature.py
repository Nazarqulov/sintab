"""Samsara webhook imzosi: HMAC-SHA256(base64-decode(secret), "v1:<timestamp>:<body>").

Sarlavhalar: X-Samsara-Signature: "v1=<hex>", X-Samsara-Timestamp: unix vaqt.
"""

import base64
import binascii
import hashlib
import hmac
import time

SIGNATURE_VERSION = "v1"
MAX_CLOCK_SKEW_SECONDS = 300


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    key = base64.b64decode(secret, validate=True)
    message = f"{SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def verify_signature(
    secret: str | None,
    timestamp: str | None,
    body: bytes,
    signature: str | None,
    *,
    now: float | None = None,
) -> bool:
    if not (secret and timestamp and signature):
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if sent_at > 10**12:  # millisekundda kelgan bo'lsa
        sent_at //= 1000
    current = time.time() if now is None else now
    # Eski so'rovni qayta yuborish (replay) hujumidan himoya.
    if abs(current - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        return False
    try:
        expected = compute_signature(secret, timestamp, body)
    except (binascii.Error, ValueError):
        return False
    return hmac.compare_digest(expected, signature.strip())
