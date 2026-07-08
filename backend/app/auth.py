"""Standard-library authentication primitives: password hashing + signed tokens.

Air-gap constraint (Constitution VI): no third-party auth libraries (no PyJWT,
no passlib). Passwords are hashed with PBKDF2-HMAC-SHA256; bearer tokens are a
compact two-segment ``base64url(payload).base64url(hmac)`` structure signed with
``settings.auth_secret``. Both verification paths use ``hmac.compare_digest`` to
avoid timing side-channels.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from app.config import settings

# --- password hashing -------------------------------------------------------
_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return a self-describing ``pbkdf2_sha256$iter$salt_hex$hash_hex`` digest."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of ``password`` against a stored PBKDF2 digest."""
    if not stored:
        return False
    try:
        algo_tag, iter_s, salt_hex, hash_hex = stored.split("$")
        algo = algo_tag.split("_", 1)[1]  # "pbkdf2_sha256" -> "sha256"
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, IndexError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


# --- bearer token (compact HMAC-signed) -------------------------------------
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payload_segment: str) -> str:
    sig = hmac.new(
        settings.auth_secret.encode("utf-8"),
        payload_segment.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(sig)


def issue_token(username: str, role: str, ttl_seconds: int | None = None) -> str:
    """Mint a signed bearer token carrying ``{sub, role, exp}``."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.auth_token_ttl_seconds
    payload = {"sub": username, "role": role, "exp": int(time.time()) + ttl}
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_segment}.{_sign(payload_segment)}"


def verify_token(token: str | None) -> tuple[str, str] | None:
    """Validate signature + expiry; return ``(username, role)`` or ``None``.

    Returns ``None`` on any malformed / tampered / expired token so callers can
    treat verification failure uniformly as "unauthenticated".
    """
    if not token:
        return None
    try:
        payload_segment, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(payload_segment)):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_segment))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return None
    username = payload.get("sub")
    role = payload.get("role")
    if not isinstance(username, str) or not isinstance(role, str):
        return None
    return username, role
