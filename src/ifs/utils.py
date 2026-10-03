"""Kleine Hilfsfunktionen: UUIDv7, virtuelle Pfade, Zeitstempel."""

from __future__ import annotations

import os
import posixpath
import re
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Macht einen (ggf. naiven) Zeitstempel UTC-bewusst.

    SQLite liefert trotz `timezone=True` naive Werte; PostgreSQL bewusste.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def uuid7() -> uuid.UUID:
    """Zeitlich sortierbare UUID (Version 7) ohne externe Abhängigkeit."""
    ts_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ts_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0x2 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def normalize_path(path: str | None) -> str:
    """Normalisiert einen virtuellen Pfad (POSIX-artig, immer mit führendem '/').

    Verhindert Pfad-Traversal: '..' wird abgelehnt.
    """
    if not path:
        return "/"
    if not isinstance(path, str):
        # Nur wenn nötig allokieren; für den Normalfall (str) entfällt str().
        path = str(path)
    if "\\" in path:
        path = path.replace("\\", "/")
    if path == "/":
        return "/"
    # Schneller Pfad (häufigster Fall): keine leeren Segmente ('//', Endung '/'),
    # kein '.'/'..'-Segment (erkennbar an '/.' bzw. führendem '.') – dann ist der
    # Pfad bereits normalisiert und es genügt, den führenden Slash zu ergänzen.
    # Damit entfallen split() und join() samt Zwischenlisten.
    if (
        "//" not in path
        and "/." not in path
        and not path.startswith(".")
        and not path.endswith("/")
    ):
        return path if path.startswith("/") else "/" + path
    parts: list[str] = []
    for segment in path.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            raise ValueError("Pfad-Traversal ist nicht erlaubt")
        parts.append(segment)
    return "/" + "/".join(parts)


def join_path(parent: str, name: str) -> str:
    return posixpath.join(normalize_path(parent), name)


def parent_of(path: str) -> str:
    normalized = normalize_path(path)
    parent = posixpath.dirname(normalized)
    return parent or "/"


def basename_of(path: str) -> str:
    return posixpath.basename(normalize_path(path))


def parse_port_range(spec: str) -> range:
    """Wandelt '30000-30100' oder '30000' in ein range-Objekt."""
    spec = spec.strip()
    if "-" in spec:
        start, end = spec.split("-", 1)
        return range(int(start), int(end) + 1)
    port = int(spec)
    return range(port, port + 1)


# --- Header-Sicherheit -----------------------------------------------------

_MIME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def safe_mime(mime: str | None, default: str = "application/octet-stream") -> str:
    """Stellt sicher, dass ein MIME-Typ header-sicher und normalisiert ist.

    MIME-Typen sind case-insensitiv; die Normalisierung auf Kleinschreibung sorgt
    dafür, dass Sicherheitsentscheidungen (z. B. Attachment/Sandbox für aktive
    Inhalte) nicht durch ``TeXt/HtMl`` o. Ä. umgangen werden können.
    """
    if not mime:
        return default
    candidate = mime.strip().lower()
    if _CONTROL_RE.search(candidate):
        return default
    return candidate if _MIME_RE.match(candidate) else default


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """RFC-6266-konformer Headerwert – verhindert Header-/Zeilen-Injection."""
    safe = _CONTROL_RE.sub("", filename or "")
    # ASCII-Vergleich statt ord()-Aufruf je Zeichen; Semantik identisch
    # (32 <= ord(ch) < 127  <=>  " " <= ch <= "~").
    fallback = "".join(
        ch if " " <= ch <= "~" and ch not in '"\\' else "_" for ch in safe
    )
    fallback = fallback.strip() or "download"
    encoded = quote(safe, safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
