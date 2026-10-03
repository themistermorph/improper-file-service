"""Wiederverwendbare FastAPI-Abhängigkeiten (DB-Session, Authentifizierung)."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
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


def _authenticate(db: Session, authorization: str | None) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
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
    return user


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    user = _authenticate(db, authorization)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Nicht authentifiziert oder Token ungültig",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user_optional(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User | None:
    return _authenticate(db, authorization)


def require_admin(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if not authz.is_system_admin(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administratorrechte erforderlich")
    return user


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
