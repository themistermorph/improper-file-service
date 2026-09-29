"""Quota-Prüfung: Gesamtspeicher pro Besitzer."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import QuotaExceeded
from ..models import Entry, EntryType


def limit_bytes() -> int:
    """Konfigurierte Gesamt-Quota pro Besitzer (0 = unbegrenzt)."""
    return int(get_settings().default_quota_bytes or 0)


def usage_bytes(db: Session, owner_id: UUID) -> int:
    total = db.execute(
        select(func.coalesce(func.sum(Entry.size), 0)).where(
            Entry.owner_id == owner_id,
            Entry.type == EntryType.file,
            Entry.trashed_at.is_(None),
        )
    ).scalar_one()
    return int(total or 0)


def enforce(db: Session, owner_id: UUID, delta_bytes: int) -> None:
    """Prüft, ob das Hinzufügen von ``delta_bytes`` die Quota überschreitet."""
    limit = limit_bytes()
    if limit <= 0:
        return
    if usage_bytes(db, owner_id) + max(delta_bytes, 0) > limit:
        raise QuotaExceeded("Speicher-Quota überschritten")
