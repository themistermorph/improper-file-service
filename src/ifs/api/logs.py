"""Admin-Panel: Prozess-Logs (api/ftp/worker) lesen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core import logs
from ..models import User
from .deps import require_admin
from .schemas import LogOut, LogServiceOut

router = APIRouter(tags=["logs"])


@router.get("/logs", response_model=list[LogServiceOut])
def list_logs(_: User = Depends(require_admin)) -> list[dict]:
    """Verfügbare Prozess-Logs mit Größe/Änderungszeit."""
    return [logs.service_info(service) for service in logs.SERVICES]


@router.get("/logs/{service}", response_model=LogOut)
def get_log(
    service: str,
    lines: int = Query(default=200, ge=1, le=2000),
    _: User = Depends(require_admin),
) -> dict:
    """Letzte Zeilen der Logdatei eines Prozesses (nur Admin)."""
    if service not in logs.SERVICES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unbekannter Prozess")
    return logs.read_tail(service, lines)
