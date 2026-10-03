"""Passwort-Hashing, Tokens und JWT."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from .config import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, ValueError):
        return False


_dummy_hash_value: str | None = None


def _dummy_hash() -> str:
    global _dummy_hash_value
    if _dummy_hash_value is None:
        _dummy_hash_value = hash_password("ifs-timing-equalizer")
    return _dummy_hash_value


def verify_password_safe(password_hash: str | None, password: str) -> bool:
    """Verifiziert ein Passwort und rechnet auch bei unbekanntem Nutzer einen Hash.

    Dadurch unterscheidet sich die Antwortzeit nicht zwischen existierenden und
    nicht existierenden Benutzernamen (keine Enumeration über Timing).
    """
    return verify_password(password_hash or _dummy_hash(), password)


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "purpose": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_token(token: str, purpose: str) -> dict[str, Any] | None:
    """Dekodiert ein JWT und akzeptiert es nur für den erwarteten Zweck."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != purpose:
        return None
    return payload


def decode_access_token(token: str) -> dict[str, Any] | None:
    # Nur echte Access-Tokens akzeptieren – keine Vorschau-/Archiv-Token.
    return _decode_token(token, "access")


def create_preview_token(
    entry_id: str, user_id: str, token_version: int, ttl_seconds: int = 300
) -> str:
    """Kurzlebiger Token für die direkte Vorschau (Browser-Ressourcen)."""
    return _create_scoped_token("preview", entry_id, user_id, token_version, ttl_seconds)


def decode_preview_token(token: str) -> dict[str, Any] | None:
    return _decode_scoped_token(token, "preview")


def create_archive_token(
    entry_ids: list[str], user_id: str, token_version: int, ttl_seconds: int = 300
) -> str:
    """Kurzlebiger Token für den ZIP-Download (ein oder mehrere Einträge)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "purpose": "archive",
        "entries": [str(entry_id) for entry_id in entry_ids],
        "sub": str(user_id),
        "tv": int(token_version),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_archive_token(token: str) -> dict[str, Any] | None:
    return _decode_token(token, "archive")


def _create_scoped_token(
    purpose: str, entry_id: str, user_id: str, token_version: int, ttl_seconds: int
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "purpose": purpose,
        "entry": str(entry_id),
        "sub": str(user_id),
        "tv": int(token_version),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_scoped_token(token: str, purpose: str) -> dict[str, Any] | None:
    return _decode_token(token, purpose)
