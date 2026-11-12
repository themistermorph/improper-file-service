"""Wiederverwendbare FastAPI-Abhängigkeiten (DB-Session, Authentifizierung)."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, lazyload

from ..config import get_settings
from ..core import authz
from ..db import get_session_factory
from ..models import User
from ..security import decode_access_token


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@lru_cache(maxsize=8)
def _networks(value: str) -> tuple:
    networks = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _bearer_ip_allowed(request: Request) -> bool:
    """True, wenn die Client-IP in ``IFS_BEARER_ALLOWED_NETWORKS`` liegt."""
    peer = client_ip(request)
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        # Nicht-IP-Peers (z. B. ein Testclient ohne gesetzte Client-Adresse)
        # erhalten keinen IP-basierten Bearer-Zugriff.
        return False
    networks = _networks(get_settings().bearer_allowed_networks)
    return any(address in network for network in networks)


def bearer_allowed(db: Session, user: User, request: Request) -> bool:
    """Bearer nur aus freigegebenen Netzen oder mit Systemrecht ``api``."""
    if authz.is_system_admin(db, user) or authz.has_system_perm(db, user, authz.API):
        return True
    return _bearer_ip_allowed(request)


def _token_from_request(
    request: Request, authorization: str | None
) -> tuple[str | None, str | None]:
    """Liefert ``(token, quelle)``; ``quelle`` ist ``bearer`` oder ``cookie``."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip(), "bearer"
    cookie = request.cookies.get(get_settings().auth_cookie_name)
    if cookie:
        return cookie, "cookie"
    return None, None


def _authenticate(db: Session, request: Request, authorization: str | None) -> User | None:
    token, source = _token_from_request(request, authorization)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        user_id = UUID(str(payload["sub"]))
    except (ValueError, TypeError):
        return None
    user = db.get(User, user_id, options=[lazyload(User.groups)])
    if user is None or not user.is_active:
        return None
    if "tv" not in payload or int(payload["tv"]) != user.token_version:
        return None
    if source == "bearer" and not bearer_allowed(db, user, request):
        return None
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    user = _authenticate(db, request, authorization)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Nicht authentifiziert oder Token ungültig",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User | None:
    return _authenticate(db, request, authorization)


def require_admin(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if not authz.is_system_admin(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administratorrechte erforderlich")
    return user


def _require_system_permission(db: Session, user: User, bit: int) -> User:
    """Systemrecht oder Systemadmin (Audit/Monitor sind auch fuer Admins da)."""
    if authz.has_system_perm(db, user, bit) or authz.is_system_admin(db, user):
        return user
    name = ", ".join(authz.names_from_perms(bit, namespace="system"))
    raise HTTPException(
        status.HTTP_403_FORBIDDEN, f"Systemrecht '{name}' erforderlich"
    )


def require_audit(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return _require_system_permission(db, user, authz.AUDIT)


def require_monitor(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return _require_system_permission(db, user, authz.MONITOR)


def _trusted_networks() -> list:
    networks = []
    for part in (get_settings().trusted_proxies or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted(ip: str | None, networks: list) -> bool:
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def is_secure_request(request: Request) -> bool:
    """True, wenn die Anfrage effektiv per HTTPS kommt.

    Direkt über TLS *oder* via ``X-Forwarded-Proto: https``, aber nur wenn der
    direkte Peer ein konfigurierter vertrauenswürdiger Proxy ist.
    """
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else None
    networks = _trusted_networks()
    if peer and networks and _is_trusted(peer, networks):
        forwarded = request.headers.get("x-forwarded-proto")
        if forwarded:
            return forwarded.split(",")[0].strip().lower() == "https"
    return False


def client_ip(request: Request) -> str | None:
    """Ermittelt die Client-IP; X-Forwarded-For nur von vertrauenswürdigen Proxies."""
    peer = request.client.host if request.client else None
    networks = _trusted_networks()
    if peer and networks and _is_trusted(peer, networks):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            chain = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            for candidate in reversed(chain):
                if not _is_trusted(candidate, networks):
                    return candidate
            if chain:
                return chain[0]
    return peer
