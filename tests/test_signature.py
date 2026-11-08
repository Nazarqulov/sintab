import base64

from app.services.samsara.signature import compute_signature, verify_signature

SECRET = base64.b64encode(b"secret-key").decode()
BODY = b'{"eventId":"1"}'
NOW = 1_700_000_000


def _sign(timestamp: int = NOW, body: bytes = BODY) -> str:
    return compute_signature(SECRET, str(timestamp), body)


def test_valid_signature_is_accepted():
    assert verify_signature(SECRET, str(NOW), BODY, _sign(), now=NOW)


def test_signature_format_matches_samsara_spec():
    import hashlib
    import hmac

    expected = hmac.new(b"secret-key", f"v1:{NOW}:".encode() + BODY, hashlib.sha256).hexdigest()
    assert _sign() == f"v1={expected}"


def test_tampered_body_is_rejected():
    assert not verify_signature(SECRET, str(NOW), b'{"eventId":"2"}', _sign(), now=NOW)


def test_wrong_secret_is_rejected():
    other = base64.b64encode(b"other").decode()
    assert not verify_signature(other, str(NOW), BODY, _sign(), now=NOW)


def test_missing_headers_are_rejected():
    assert not verify_signature(SECRET, None, BODY, _sign(), now=NOW)
    assert not verify_signature(SECRET, str(NOW), BODY, None, now=NOW)
    assert not verify_signature(None, str(NOW), BODY, _sign(), now=NOW)


def test_stale_timestamp_is_rejected_as_replay():
    old = NOW - 301
    assert not verify_signature(SECRET, str(old), BODY, _sign(old), now=NOW)


def test_millisecond_timestamp_is_accepted():
    ms = NOW * 1000
    assert verify_signature(SECRET, str(ms), BODY, _sign(ms), now=NOW)


def test_non_numeric_timestamp_is_rejected():
    assert not verify_signature(SECRET, "abc", BODY, "v1=00", now=NOW)


def test_non_base64_secret_is_rejected_not_crashing():
    assert not verify_signature("not base64!!", str(NOW), BODY, "v1=00", now=NOW)
