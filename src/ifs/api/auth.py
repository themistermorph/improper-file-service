"""Authentifizierungs-Endpunkte."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core import audit, ratelimit
from ..db import session_scope
from ..models import User
from ..security import create_access_token, verify_password_safe
from ..utils import utcnow
from .deps import client_ip, get_current_user, get_db
from .schemas import LoginRequest, TokenResponse, UserOut

router = APIRouter(tags=["auth"])


def _audit_failure(request: Request, username: str, result: str = "denied") -> None:
    with session_scope() as session:
        audit.record(
            session,
            "auth.login.failed",
            protocol="http",
            ip=client_ip(request),
            result=result,
            details={"username": username},
        )


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    account_key, ip_key = ratelimit.keys(client_ip(request), payload.username)
    retry = ratelimit.retry_after(account_key, ip_key)
    if retry:
        _audit_failure(request, payload.username, result="locked")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Fehlversuche – bitte später erneut versuchen.",
            headers={"Retry-After": str(retry)},
        )

    user = db.execute(select(User).where(User.username == payload.username)).scalars().first()
    # Immer einen Hash prüfen (auch bei unbekanntem Nutzer) -> kein Timing-Leak.
    valid = verify_password_safe(user.password_hash if user is not None else None, payload.password)
    if user is None or not user.is_active or not valid:
        ratelimit.record_failure(account_key, ip_key)
        _audit_failure(request, payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzername oder Passwort falsch")

    ratelimit.record_success(account_key, ip_key)
    audit.record(
        db, "auth.login", actor_id=user.id, protocol="http", ip=client_ip(request)
    )
    user.last_login_at = utcnow()
    settings = get_settings()
    token = create_access_token(
        str(user.id),
        {"username": user.username, "admin": user.is_admin, "tv": user.token_version},
    )
    return TokenResponse(access_token=token, expires_in=settings.access_token_ttl_seconds)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
