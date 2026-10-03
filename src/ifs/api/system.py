"""System-Endpunkte: Health, Version, Metriken."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..core import authz, system_stats
from ..models import User
from .deps import get_current_user_optional, get_db

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz() -> dict:
    """Minimaler Liveness-/Readiness-Check (keine internen Zähler)."""
    return {"status": "ok"}


@router.get("/version")
def version() -> dict:
    """Versionsinfo – bewusst ohne Umgebungsangabe (kein Info-Leak ohne Auth)."""
    settings = get_settings()
    return {
        "name": settings.app_name,
        "version": __version__,
    }


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> str:
    """Prometheus-Metriken. Standardmäßig nur für Admins; per Setting öffentlich."""
    settings = get_settings()
    if not settings.metrics_public:
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht authentifiziert")
        if not authz.is_system_admin(db, user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Administratorrechte erforderlich")

    ifs = system_stats.ifs_metrics(db)
    lines = [
        "# HELP ifs_users Anzahl Benutzer",
        "# TYPE ifs_users gauge",
        f"ifs_users {ifs['users']}",
        "# HELP ifs_entries Anzahl Namespace-Einträge",
        "# TYPE ifs_entries gauge",
        f"ifs_entries {ifs['entries']}",
        "# HELP ifs_stored_bytes Summe Dateigrößen",
        "# TYPE ifs_stored_bytes gauge",
        f"ifs_stored_bytes {ifs['stored_bytes']}",
    ]
    return "\n".join(lines) + "\n"
