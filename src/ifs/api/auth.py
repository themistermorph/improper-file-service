"""Authentifizierungs-Endpunkte."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from ..config import get_settings
from ..core import audit, authz, ratelimit
from ..db import session_scope
from ..models import User
from ..security import create_access_token, verify_password_safe
from ..utils import utcnow
from .deps import (
    bearer_allowed,
    client_ip,
    get_current_user,
    get_current_user_optional,
    get_db,
    is_secure_request,
)
from .schemas import LoginRequest, MeOut, SessionStatus, TokenResponse

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
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    issue_token: bool = True,
    db: Session = Depends(get_db),
) -> TokenResponse:
    account_key, ip_key = ratelimit.keys(client_ip(request), payload.username)
    retry = ratelimit.retry_after(account_key, ip_key)
    if retry:
        _audit_failure(request, payload.username, result="locked")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Fehlversuche – bitte später erneut versuchen.",
            headers={"Retry-After": str(retry)},
        )

    # Gruppen werden hier nicht gebraucht: gezielt ohne Selectin-Load laden
    # (Login-Antwort und Audit nutzen nur Skalarwerte des Benutzers).
    user = (
        db.execute(
            select(User)
            .where(User.username == payload.username)
            .options(lazyload(User.groups))
        )
        .scalars()
        .first()
    )
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
    # Die Web-UI erhaelt die Sitzung ausschliesslich als HttpOnly-Cookie. Das
    # Cookie wird immer gesetzt; der Token im Response-Body ist ein Opt-in fuer
    # API-Clients (Standard) und wird nur aus freigegebenen Netzen bzw. fuer
    # Konten mit Systemrecht `api` ausgeliefert.
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        secure=is_secure_request(request),
        samesite="strict",
        path="/",
    )
    if not issue_token or not bearer_allowed(db, user, request):
        return TokenResponse(
            access_token=None,
            token_type="cookie",
            expires_in=settings.access_token_ttl_seconds,
        )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    """Loescht das Sitzungs-Cookie (idempotent, auch ohne gueltige Sitzung)."""
    settings = get_settings()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        httponly=True,
        secure=is_secure_request(request),
        samesite="strict",
    )
    return response


@router.get("/auth/session", response_model=SessionStatus)
def session_status(
    user: User | None = Depends(get_current_user_optional),
) -> SessionStatus:
    """Prueft die Cookie-Session ohne 401, damit der Login-Screen ruhig bleibt."""
    return SessionStatus(authenticated=user is not None)


@router.get("/me", response_model=MeOut)
def me(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MeOut:
    """Eigene Sicht mit effektiven Systemrechten.

    ``is_admin`` ist bewusst effektiv (Adminrolle *oder* Flag), damit die UI
    dieselbe Bedingung wie die API prüft. ``permissions`` enthält nur
    ``audit``/``monitor`` – Admin wird über ``is_admin`` ausgedrückt.
    """
    perms = authz.system_perms(db, user) & authz.SYSTEM_ASSIGNABLE
    return MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_admin=authz.is_system_admin(db, user),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        permissions=authz.names_from_perms(perms, namespace="system"),
    )
