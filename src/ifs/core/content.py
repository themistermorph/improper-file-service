"""Content/Blob-Schicht: Zugriff auf den S3-kompatiblen Objektspeicher.

S3 enthält ausschließlich unveränderliche, content-addressed Blobs.
Die Zuordnung zu Namespace und Rechten erfolgt ausschließlich über die DB.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections import deque
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO, Self

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Blob, BlobStatus
from ..utils import safe_mime

CHUNK_SIZE = 1024 * 1024
# Untergrenze für die HTTP-Stream-Blockgröße: darunter dominiert der
# Syscall-/ASGI-Overhead gegenüber dem eigentlichen Datentransfer.
MIN_STREAM_CHUNK = 64 * 1024
# Obergrenze für parallele S3-Range-Requests pro Download. Jeder zusätzliche
# Stream belegt einen Thread und einen S3-Pool-Slot.
MAX_DOWNLOAD_PARALLELISM = 16
# S3 verlangt bei Multipart-Uploads mindestens 5 MiB pro Part (außer dem
# letzten). Die Part-Größe paralleler HTTP-Uploads wird darauf angehoben.
MIN_MULTIPART_PART = 5 * 1024 * 1024
MAX_UPLOAD_PART = 64 * 1024 * 1024


@dataclass
class BlobInfo:
    storage_key: str
    sha256: str
    size: int


class SpoolRef(str):
    """Spool-Pfad mit bereits berechnetem SHA-256 und bekannter Größe.

    Der Upload-Stream wird ohnehin einmal vollständig gelesen; der dabei
    ermittelte Digest wird hier zusammen mit dem Pfad transportiert, damit
    :func:`store_blob` die Datei nicht erneut vollständig hashen muss. Als
    ``str``-Subklasse bleibt der Aufruf für bestehende (auch dreiparametrige)
    Implementierungen unverändert gültig.
    """

    __slots__ = ("sha256", "size")

    def __new__(cls, path: str, sha256: str, size: int) -> Self:
        ref = super().__new__(cls, path)
        ref.sha256 = sha256
        ref.size = size
        return ref


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
        config=Config(
            signature_version="s3v4",
            # Mehr parallele Transfers ohne Stau im Connection-Pool.
            max_pool_connections=max(1, settings.s3_max_pool_connections),
            s3={"addressing_style": "path"},
        ),
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
    # readinto vermeidet eine Bytes-Neuanlage pro Block (Speicher-Allokationen
    # im Hot-Path großer Dateien).
    digest = hashlib.sha256()
    buffer = bytearray(CHUNK_SIZE)
    view = memoryview(buffer)
    with open(path, "rb") as handle:
        while True:
            n = handle.readinto(buffer)
            if not n:
                break
            digest.update(view[:n])
    return digest.hexdigest()


def sha256_parts(paths: Sequence[str]) -> str:
    """Berechnet den SHA-256 über mehrere Part-Dateien in Dateireihenfolge."""
    digest = hashlib.sha256()
    buffer = bytearray(CHUNK_SIZE)
    view = memoryview(buffer)
    for path in paths:
        with open(path, "rb") as handle:
            while True:
                n = handle.readinto(buffer)
                if not n:
                    break
                digest.update(view[:n])
    return digest.hexdigest()


class PartReader:
    """Datei-ähnlicher Reader über mehrere Part-Dateien (ohne Zusammenkopieren).

    Wird an ``put_object``/``upload_fileobj`` übergeben. Dadurch kann die
    S3-Finalisierung eines parallelen Uploads direkt aus den Parts lesen,
    statt vorher eine weitere vollständige Kopie im Spool-Verzeichnis anzulegen.
    ``seek``/``tell`` werden unterstützt, weil boto3 die Größe des Streams
    ermittelt und Multipart-Teile positioniert liest.
    """

    def __init__(self, paths: Sequence[str]) -> None:
        self._paths = list(paths)
        self._sizes = [os.path.getsize(path) for path in self._paths]
        self._total = sum(self._sizes)
        self._starts: list[int] = []
        offset = 0
        for size in self._sizes:
            self._starts.append(offset)
            offset += size
        self._pos = 0
        self._index = 0
        self._handle: BinaryIO | None = None
        self._handle_pos = 0
        if self._paths:
            self._open(0)

    @property
    def total(self) -> int:
        return self._total

    def _open(self, index: int) -> None:
        self.close()
        self._index = index
        self._handle = open(self._paths[index], "rb")
        self._handle_pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_CUR:
            offset += self._pos
        elif whence == os.SEEK_END:
            offset += self._total
        if offset < 0:
            raise ValueError("negativer Seek-Offset")
        offset = min(offset, self._total)
        # Part-Index per binärer Suche bestimmen.
        lo, hi = 0, len(self._starts) - 1
        index = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._starts[mid] <= offset:
                index = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if self._paths:
            if self._handle is None or index != self._index:
                self._open(index)
            local = offset - self._starts[index]
            self._handle.seek(local)
            self._handle_pos = local
        self._pos = offset
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._total - self._pos
        if size == 0 or self._pos >= self._total:
            return b""
        chunks = bytearray()
        remaining = size
        while remaining > 0 and self._pos < self._total:
            if self._handle is None or self._index >= len(self._paths):
                break
            available = self._sizes[self._index] - self._handle_pos
            if available <= 0:
                self._index += 1
                if self._index >= len(self._paths):
                    self._handle = None
                    break
                self._open(self._index)
                continue
            data = self._handle.read(min(remaining, available))
            if not data:
                break
            chunks.extend(data)
            n = len(data)
            self._handle_pos += n
            self._pos += n
            remaining -= n
            if self._handle_pos >= self._sizes[self._index]:
                self._index += 1
                if self._index < len(self._paths):
                    self._open(self._index)
                else:
                    self._handle = None
        return bytes(chunks)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def upload_parts(
    paths: Sequence[str], sha256: str, mime: str | None = None
) -> BlobInfo:
    """Überträgt mehrere Part-Dateien als ein S3-Objekt (CAS-Schlüssel)."""
    settings = get_settings()
    storage_key = f"cas/{sha256[:2]}/{sha256}"
    size = sum(os.path.getsize(path) for path in paths)
    mib = 1024 * 1024
    threshold = max(1, settings.s3_multipart_threshold_mb) * mib
    extra_args = _extra_args(mime)
    client = get_s3_client()
    reader = PartReader(paths)
    try:
        if size < threshold:
            client.put_object(
                Bucket=settings.s3_bucket,
                Key=storage_key,
                Body=reader,
                ContentLength=size,
                **extra_args,
            )
        else:
            transfer_config = TransferConfig(
                multipart_threshold=threshold,
                multipart_chunksize=max(1, settings.s3_multipart_chunksize_mb) * mib,
                max_concurrency=max(1, settings.s3_transfer_concurrency),
            )
            client.upload_fileobj(
                reader,
                settings.s3_bucket,
                storage_key,
                ExtraArgs=extra_args,
                Config=transfer_config,
            )
    finally:
        reader.close()
    return BlobInfo(storage_key=storage_key, sha256=sha256, size=size)


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
    mib = 1024 * 1024
    threshold = max(1, settings.s3_multipart_threshold_mb) * mib
    extra_args = _extra_args(mime)
    client = get_s3_client()
    if size < threshold:
        # Kleine Objekte: direkter PUT statt TransferManager. Der Manager
        # erzeugt für jeden Upload einen Thread-Pool/Future und kostet bei
        # Kleindateien mehr Zeit als der eigentliche S3-Request.
        with open(local_path, "rb") as handle:
            client.put_object(
                Bucket=settings.s3_bucket,
                Key=storage_key,
                Body=handle,
                ContentLength=size,
                **extra_args,
            )
    else:
        transfer_config = TransferConfig(
            multipart_threshold=threshold,
            multipart_chunksize=max(1, settings.s3_multipart_chunksize_mb) * mib,
            max_concurrency=max(1, settings.s3_transfer_concurrency),
        )
        client.upload_file(
            local_path,
            settings.s3_bucket,
            storage_key,
            ExtraArgs=extra_args,
            Config=transfer_config,
        )
    return BlobInfo(storage_key=storage_key, sha256=sha256, size=size)


def _precomputed_sha256(local_path: str) -> str | None:
    """Liefert einen an einem :class:`SpoolRef` mitgeführten Digest.

    Der Digest wird nur verwendet, wenn die Dateigröße unverändert passt –
    sonst würde ein veralteter Hash den CAS-Schlüssel verfälschen. In diesem
    Fall (und ohne SpoolRef) wird die Datei wie bisher vollständig gehasht.
    """
    carried = getattr(local_path, "sha256", None)
    if carried is None:
        return None
    size = getattr(local_path, "size", None)
    if size is not None:
        try:
            if os.path.getsize(local_path) != size:
                return None
        except OSError:
            return None
    return carried


def store_blob(
    db: Session, local_path: str, mime: str | None = None, sha256: str | None = None
) -> Blob:
    """Legt einen Blob an – mit Dedup über den SHA-256 (CAS).

    ``sha256`` kann ein bereits berechneter Digest der Datei sein (z. B. aus
    dem Upload-Stream). Fehlt er, wird die Datei wie bisher über
    :func:`sha256_file` gelesen; ein über :class:`SpoolRef` mitgeführter
    Digest wird ebenfalls genutzt, sofern er noch zur Dateigröße passt.
    """
    settings = get_settings()
    mime = safe_mime(mime) if mime else None
    if sha256 is None:
        sha256 = _precomputed_sha256(local_path)
    if sha256 is None:
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


def store_blob_parts(
    db: Session, paths: Sequence[str], mime: str | None = None, sha256: str | None = None
) -> Blob:
    """Legt einen Blob aus mehreren Part-Dateien an (CAS + Dedup wie store_blob).

    Der Digest wird – falls nicht übergeben – einmal sequenziell über die Parts
    gebildet. Anschließend liest der S3-Upload die Parts direkt über
    :class:`PartReader`; es entsteht keine zusätzliche Vollkopie im Spool.
    """
    if not paths:
        raise ValueError("keine Parts übergeben")
    settings = get_settings()
    mime = safe_mime(mime) if mime else None
    if sha256 is None:
        sha256 = sha256_parts(paths)
    storage_key = f"cas/{sha256[:2]}/{sha256}"

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

    info = upload_parts(paths, sha256, mime)

    if existing is not None:
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


def _stream_chunk_size(value: int | None = None) -> int:
    if value is None or value <= 0:
        value = get_settings().http_stream_chunk_bytes
    return max(MIN_STREAM_CHUNK, int(value))


def _download_parallelism(value: int | None = None) -> int:
    if value is None or value <= 0:
        value = get_settings().http_download_parallelism
    return max(1, min(MAX_DOWNLOAD_PARALLELISM, int(value)))


def _read_exact_range(storage_key: str, start: int, end: int) -> bytes:
    """Liest einen S3-Bereich vollständig in den Speicher (ein Prefetch-Fenster)."""
    response = get_object(storage_key, start, end)
    body = response["Body"]
    expected = end - start + 1
    try:
        data = body.read(expected)
    finally:
        close = getattr(body, "close", None)
        if close is not None:
            close()
    if len(data) != expected:
        raise OSError(
            f"S3-Range unvollständig: {len(data)} statt {expected} Bytes "
            f"(bytes={start}-{end})"
        )
    return data


def iter_object(
    storage_key: str,
    *,
    start: int = 0,
    end: int | None = None,
    total_size: int | None = None,
    chunk_size: int | None = None,
    parallelism: int | None = None,
) -> Iterator[bytes]:
    """Streamt ein S3-Objekt als geordnete Byte-Blöcke zum Client.

    Bei ``parallelism > 1`` wird der angeforderte Bereich in Fenster geteilt
    und mit mehreren S3-Range-Requests **vorgezogen** gelesen. Die Ausgabe
    bleibt streng in Dateireihenfolge; es werden höchstens ``parallelism``
    Fenster gleichzeitig gehalten. Das beschleunigt einen einzelnen
    HTTP-Download, wenn der Objektspeicher pro Verbindung limitiert ist
    (auf dem Testsystem z. B. ~40 MB/s single vs. ~200 MB/s mit 4 Streams),
    ohne die Range-/Resume-Semantik zu verändern.
    """
    chunk = _stream_chunk_size(chunk_size)
    par = _download_parallelism(parallelism)
    if total_size is not None and end is None:
        end = max(total_size - 1, 0)
    if end is None:
        end = start - 1  # leerer Bereich -> nichts streamen
    if end < start:
        return

    if par == 1 or end - start + 1 <= chunk:
        response = get_object(storage_key, start if start else None, end)
        yield from stream_out(response["Body"], chunk)
        return

    ranges = [
        (pos, min(pos + chunk - 1, end))
        for pos in range(start, end + 1, chunk)
    ]

    # ``ThreadPoolExecutor`` konzentriert die S3-Reads in eigenen Threads; der
    # aufrufende Streaming-Thread wartet nur auf das jeweils nächste Fenster.
    # Das ist wichtig, weil Starlette synchrone Generatoren ohnehin in einem
    # Worker-Thread iteriert – so blockiert kein Netzzugriff den Event-Loop.
    with ThreadPoolExecutor(max_workers=par, thread_name_prefix="ifs-s3-prefetch") as pool:
        pending: deque = deque()
        iterator = iter(ranges)
        for _ in range(min(par, len(ranges))):
            window = next(iterator)
            pending.append(pool.submit(_read_exact_range, storage_key, *window))
        while pending:
            future = pending.popleft()
            yield future.result()
            try:
                window = next(iterator)
            except StopIteration:
                continue
            pending.append(pool.submit(_read_exact_range, storage_key, *window))


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


def remove_spool(path: str | None) -> None:
    if not path:
        return
    # Ein einzelner Syscall statt exists()-Prüfung + remove().
    try:
        os.remove(path)
    except OSError:
        pass


def stream_out(body: BinaryIO, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    # `read` einmal binden (spart einen Attribut-Lookup pro Block); die
    # Default-Blockgröße bleibt 1 MiB (RAM-schonend pro Stream).
    read = body.read
    yield from iter(lambda: read(chunk_size), b"")
