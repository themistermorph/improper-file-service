"""Hintergrund-Worker: Outbox verarbeiten, Papierkorb leeren, Blobs aufräumen."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from .config import get_settings
from .core import content
from .db import session_scope
from .models import Blob, BlobStatus, Entry, Outbox, UploadSession, UploadStatus, Version
from .utils import utcnow

logger = logging.getLogger("ifs.worker")

UPLOAD_SESSION_TTL_HOURS = 24


def process_outbox() -> int:
    processed = 0
    batch_size = get_settings().worker_outbox_batch_size
    with session_scope() as db:
        rows = db.execute(
            select(Outbox)
            .where(Outbox.published_at.is_(None))
            .order_by(Outbox.created_at)
            .limit(batch_size)
        ).scalars().all()
        # Ein Zeitstempel für den gesamten Batch (statt utcnow() je Zeile).
        now = utcnow()
        for row in rows:
            logger.info("Event verarbeitet: %s %s", row.event_type, row.payload)
            row.published_at = now
            processed += 1
    return processed


def purge_trash() -> int:
    cutoff = utcnow() - timedelta(days=get_settings().trash_retention_days)
    removed = 0
    # Parent-Zuordnung per Subquery prüfen statt db.get() je Eintrag (N+1).
    trashed_parent = aliased(Entry)
    with session_scope() as db:
        entries = db.execute(
            select(Entry).where(
                Entry.trashed_at.isnot(None),
                Entry.trashed_at < cutoff,
                or_(
                    Entry.parent_id.is_(None),
                    Entry.parent_id.notin_(
                        select(trashed_parent.id).where(
                            trashed_parent.trashed_at.isnot(None)
                        )
                    ),
                ),
            )
        ).scalars().all()
        for entry in entries:
            # nur Wurzeln des Papierkorbs löschen; Kinder via Kaskade
            db.delete(entry)
            removed += 1
    return removed


def abort_stale_uploads() -> int:
    cutoff = utcnow() - timedelta(hours=UPLOAD_SESSION_TTL_HOURS)
    aborted = 0
    with session_scope() as db:
        sessions = db.execute(
            select(UploadSession).where(
                UploadSession.status == UploadStatus.pending,
                UploadSession.updated_at < cutoff,
            )
        ).scalars().all()
        for session in sessions:
            content.remove_spool(session.spool_path)
            session.status = UploadStatus.aborted
            aborted += 1
    return aborted


def gc_blobs() -> int:
    # Nur unreferenzierte Blobs sperren (FOR UPDATE SKIP LOCKED) und DB-seitig
    # löschen; die S3-Objekte werden erst nach dem Commit entfernt. Ein paralleler
    # Dedup-Upload kann den Blob bis dahin referenzieren – dann greift der
    # Fremdschlüssel und der Upload schlägt fehl, statt auf ein bereits gelöschtes
    # Objekt zu verweisen.
    keys: list[str] = []
    with session_scope() as db:
        # Referenzprüfung per NOT EXISTS in der DB statt alle blob_ids in Python
        # zu laden; spart einen vollständigen Scan über `versions`.
        blobs = db.execute(
            select(Blob)
            .where(
                Blob.status == BlobStatus.ready,
                ~select(Version.id).where(Version.blob_id == Blob.id).exists(),
            )
            .with_for_update(skip_locked=True)
        ).scalars().all()
        # Nach dem Sperren erneut prüfen: In der Zwischenzeit referenzierte Blobs
        # bleiben erhalten.
        used_now = set(db.execute(select(Version.blob_id)).scalars().all())
        for blob in blobs:
            if blob.id in used_now:
                continue
            keys.append(blob.storage_key)
            db.delete(blob)
        db.flush()

    for storage_key in keys:
        try:
            content.delete_object(storage_key)
        except Exception:
            logger.exception("Blob konnte nicht aus S3 entfernt werden: %s", storage_key)
    return len(keys)


def run_once() -> None:
    events = process_outbox()
    aborted = abort_stale_uploads()
    trashed = purge_trash()
    blobs = gc_blobs()
    if events or aborted or trashed or blobs:
        logger.info(
            "Worker-Lauf: %s Events, %s Uploads abgebrochen, %s Einträge entsorgt, %s Blobs entfernt",
            events,
            aborted,
            trashed,
            blobs,
        )


def run_worker(interval_seconds: int | None = None) -> None:
    interval = (
        interval_seconds if interval_seconds is not None else get_settings().worker_interval_seconds
    )
    logger.info("IFS-Worker gestartet (Intervall %ss)", interval)
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Worker-Lauf fehlgeschlagen")
        time.sleep(interval)
