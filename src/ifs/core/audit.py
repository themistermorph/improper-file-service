"""Audit-Log – append-only, Schreibzugriff nur über den Core."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, User

# Harte Obergrenze je Abfrage – schützt das Panel vor übergroßen Antworten.
_MAX_LIMIT = 500


def record(
    db: Session,
    action: str,
    *,
    actor_id: UUID | None = None,
    target_entry: UUID | None = None,
    protocol: str = "http",
    ip: str | None = None,
    result: str = "ok",
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_entry=target_entry,
        protocol=protocol,
        ip=ip,
        result=result,
        details=details or {},
    )
    db.add(entry)
    return entry


def list_recent(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
) -> list[tuple[AuditLog, str | None]]:
    """Neueste Audit-Einträge, optional nach Aktion gefiltert.

    Reihenfolge (absteigend nach ``ts``) und Pagination bleiben unverändert.
    Die Actor-Benutzernamen kommen per OUTER JOIN aus demselben Statement –
    das spart den zweiten Roundtrip der bisherigen Einzelabfrage.
    """
    limit = min(max(int(limit), 1), _MAX_LIMIT)
    offset = max(int(offset), 0)
    stmt = select(AuditLog, User.username).outerjoin(User, AuditLog.actor_id == User.id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.execute(
        stmt.order_by(AuditLog.ts.desc()).limit(limit).offset(offset)
    ).all()
    return [(row[0], row[1]) for row in rows]
