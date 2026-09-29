"""Audit-Log – append-only, Schreibzugriff nur über den Core."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ..models import AuditLog


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
