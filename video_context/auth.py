"""Single-tenant API-key access with short-lived HttpOnly browser sessions."""
import hashlib
import hmac
import secrets
import time


def issue_session(key):
    payload = f"{int(time.time()) + 43200}:{secrets.token_hex(16)}"
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def valid_session(key, token):
    try:
        expiry, nonce, signature = token.split(":")
        payload = f"{expiry}:{nonce}"
        expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return int(expiry) > time.time() and hmac.compare_digest(signature.encode(), expected.encode())
    except (ValueError, AttributeError):
        return False
