"""Ereignis-Ausgangskorb (Transactional Outbox)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Outbox


def emit(db: Session, event_type: str, payload: dict | None = None) -> Outbox:
    event = Outbox(event_type=event_type, payload=payload or {})
    db.add(event)
    return event
