"""Content/Blob-Schicht: Zugriff auf den S3-kompatiblen Objektspeicher.

S3 enthält ausschließlich unveränderliche, content-addressed Blobs.
Die Zuordnung zu Namespace und Rechten erfolgt ausschließlich über die DB.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Blob, BlobStatus
from ..utils import safe_mime

CHUNK_SIZE = 1024 * 1024


@dataclass
class BlobInfo:
    storage_key: str
    sha256: str
    size: int


@lru_cache
def get_s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        use_ssl=settings.s3_use_ssl,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def reset_client_cache() -> None:
    get_s3_client.cache_clear()


def ensure_bucket() -> None:
    settings = get_settings()
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _extra_args(mime: str | None) -> dict:
    settings = get_settings()
    args: dict = {}
    if settings.s3_sse:
        args["ServerSideEncryption"] = settings.s3_sse
    if mime:
        args["ContentType"] = mime
    return args


def object_exists(storage_key: str) -> bool:
    settings = get_settings()
    try:
        get_s3_client().head_object(Bucket=settings.s3_bucket, Key=storage_key)
        return True
    except ClientError:
        return False


def upload_file(local_path: str, sha256: str, mime: str | None = None) -> BlobInfo:
    settings = get_settings()
    storage_key = f"cas/{sha256[:2]}/{sha256}"
    size = os.path.getsize(local_path)
    get_s3_client().upload_file(
        local_path, settings.s3_bucket, storage_key, ExtraArgs=_extra_args(mime)
    )
    return BlobInfo(storage_key=storage_key, sha256=sha256, size=size)


def store_blob(db: Session, local_path: str, mime: str | None = None) -> Blob:
    """Legt einen Blob an – mit Dedup über den SHA-256 (CAS)."""
    settings = get_settings()
    mime = safe_mime(mime) if mime else None
    sha256 = sha256_file(local_path)
    storage_key = f"cas/{sha256[:2]}/{sha256}"

    # Die CAS-Zeile ist über storage_key eindeutig. Deshalb gezielt danach
    # suchen (nicht über sha256), damit ein zweiter Upload nie eine zweite
    # Zeile mit demselben UNIQUE-Key anlegt.
    existing = db.execute(
        select(Blob).where(Blob.storage_key == storage_key)
    ).scalars().first()

    if (
        settings.cas_dedup
        and existing is not None
        and existing.status == BlobStatus.ready
        and object_exists(storage_key)
    ):
        return existing

    info = upload_file(local_path, sha256, mime)

    if existing is not None:
        # Objekt fehlte (oder Dedup deaktiviert): vorhandene Zeile aktualisieren,
        # statt mit identischem storage_key zu inserten (sonst IntegrityError).
        existing.sha256 = info.sha256
        existing.size = info.size
        existing.status = BlobStatus.ready
        db.flush()
        return existing

    blob = Blob(
        storage_key=info.storage_key,
        sha256=info.sha256,
        size=info.size,
        status=BlobStatus.ready,
    )
    try:
        # Savepoint: Ein paralleler identischer Upload kann die Zeile zwischen
        # Suche und Insert angelegt haben. Dann den IntegrityError abfangen und
        # die bereits vorhandene Zeile zurückgeben (idempotenter Upload).
        with db.begin_nested():
            db.add(blob)
            db.flush()
    except IntegrityError:
        existing = db.execute(
            select(Blob).where(Blob.storage_key == storage_key)
        ).scalars().first()
        if existing is None:
            raise
        return existing
    return blob


def get_object(storage_key: str, start: int | None = None, end: int | None = None):
    """Liefert die rohe boto3-Antwort; Body ist der Streaming-Body."""
    settings = get_settings()
    kwargs: dict = {"Bucket": settings.s3_bucket, "Key": storage_key}
    if start is not None or end is not None:
        range_spec = f"bytes={start or 0}-{end if end is not None else ''}"
        kwargs["Range"] = range_spec
    return get_s3_client().get_object(**kwargs)


def delete_object(storage_key: str) -> None:
    settings = get_settings()
    get_s3_client().delete_object(Bucket=settings.s3_bucket, Key=storage_key)


def presign_get(storage_key: str, expires: int | None = None, filename: str | None = None) -> str:
    settings = get_settings()
    params: dict = {"Bucket": settings.s3_bucket, "Key": storage_key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires or settings.s3_presign_ttl_seconds,
    )


def stream_to_spool(chunks: Iterator[bytes]) -> tuple[str, int, str]:
    """Schreibt einen eingehenden Stream in eine Temp-Datei.

    Liefert (Pfad, Größe, SHA-256). Der Aufrufer ist für das Löschen zuständig.
    """
    digest = hashlib.sha256()
    size = 0
    spool_dir = get_settings().upload_spool_dir
    os.makedirs(spool_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="ifs-upload-", dir=spool_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise
    return path, size, digest.hexdigest()


def new_spool_path() -> str:
    """Erzeugt eine leere Temp-Datei im Spool-Verzeichnis und liefert den Pfad."""
    spool_dir = get_settings().upload_spool_dir
    os.makedirs(spool_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="ifs-upload-", dir=spool_dir)
    os.close(fd)
    return path


def remove_spool(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def stream_out(body: BinaryIO, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    yield from iter(lambda: body.read(chunk_size), b"")
