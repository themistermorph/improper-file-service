"""Admin-Monitoring: System- und IFS-Metriken."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core import system_stats
from ..models import User
from .deps import get_db, require_admin

router = APIRouter(tags=["monitor"])


@router.get("/system/stats")
def system_stats_endpoint(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    """CPU/RAM/Disk/Netzwerk + IFS-Zähler + SeaweedFS (nur Admin)."""
    settings = get_settings()
    return system_stats.collect(
        db, settings.monitor_disk_path, settings.seaweedfs_status_url
    )
