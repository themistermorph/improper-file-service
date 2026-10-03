"""Kurzlebige Vorschau-Tokens und direkter Vorschau-Stream.

Bilder, Video, Audio und PDF werden über den Browser-eigenen Renderer dargestellt.
Da Browser keine Authorization-Header für `<img>`/`<video>`/`<iframe>` senden können,
wird ein kurzlebiger, signierter Token in die URL eingebettet.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session, lazyload

from ..core import authz, namespace
from ..models import User
from ..security import create_preview_token, decode_preview_token
from .deps import get_current_user, get_db
from .entries import resolve_blob, stream_blob
from .schemas import PreviewTokenOut

router = APIRouter(tags=["preview"])

PREVIEW_TTL_SECONDS = 300
# Typen, die im Browser aktiv gerendert werden könnten – per CSP sandboxen.
SANDBOXED_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/xml",
    "application/xml",
    "application/xslt+xml",
)


@router.post("/entries/{entry_id}/preview-token", response_model=PreviewTokenOut)
def create_token(
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreviewTokenOut:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "read", entry)
    resolve_blob(db, entry)  # stellt sicher, dass ein Dateiinhalt existiert
    token = create_preview_token(
        str(entry.id), str(user.id), user.token_version, PREVIEW_TTL_SECONDS
    )
    return PreviewTokenOut(
        token=token,
        url=f"/api/preview/{token}",
        expires_in=PREVIEW_TTL_SECONDS,
        name=entry.name,
        mime=entry.mime,
        size=entry.size,
    )


@router.get("/preview/{token}")
def preview(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    payload = decode_preview_token(token)
    if payload is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Ungültiger oder abgelaufener Vorschau-Token"
        )
    try:
        entry_id = UUID(str(payload["entry"]))
        user_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiger Vorschau-Token")

    entry = namespace.get_entry(db, entry_id)
    # Gruppen erst bei Bedarf laden (Eigentümer-Fall kommt ohne aus) –
    # ``authorize`` fordert sie nur für fremde Freigaben an.
    user = db.get(User, user_id, options=[lazyload(User.groups)])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer unbekannt oder inaktiv")
    if int(payload.get("tv", -1)) != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Vorschau-Token ist nicht mehr gültig")
    authz.authorize(db, user, "read", entry)

    response = stream_blob(db, entry, resolve_blob(db, entry), request, actor_id=user.id, inline_active=True)
    if (entry.mime or "").lower() in SANDBOXED_TYPES:
        response.headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:"
        )
    return response
