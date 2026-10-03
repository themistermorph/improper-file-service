"""Gemeinsame Helfer für die Test-Suite.

Zentralisiert Funktionen, die zuvor in vielen Testdateien dupliziert wurden:
Login/Authorization-Header und den Fake-Objektspeicher (``store_blob``/
``get_object``).
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from ifs.models import Blob, BlobStatus

# In-Memory-Objektspeicher (storage_key -> Rohdaten). Der Fake-``get_object``
# liest hieraus; ``reset_content`` leert ihn vor jedem Test (siehe conftest).
CONTENT: dict[str, bytes] = {}


def reset_content() -> None:
    CONTENT.clear()


def login(client: TestClient, username: str = "admin", password: str = "admin") -> str:
    """Meldet an und liefert das Access-Token."""
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]


def auth(
    client: TestClient, username: str = "admin", password: str = "admin"
) -> dict[str, str]:
    """Meldet an und liefert die fertigen ``Authorization``-Header."""
    return {"Authorization": f"Bearer {login(client, username, password)}"}


def fake_store_blob(db: Any, local_path: str, mime: str | None = None) -> Blob:
    """Ersetzt ``content.store_blob``: legt einen ``Blob`` an und merkt den Inhalt."""
    with open(local_path, "rb") as handle:
        data = handle.read()
    sha = hashlib.sha256(data).hexdigest()
    key = f"cas/{sha[:2]}/{sha}"
    CONTENT[key] = data
    blob = Blob(storage_key=key, sha256=sha, size=len(data), status=BlobStatus.ready)
    db.add(blob)
    db.flush()
    return blob


def fake_get_object(key: str, start: int | None = None, end: int | None = None) -> dict:
    """Ersetzt ``content.get_object``: liefert die zuvor gemerkten Bytes (Range-fähig)."""
    data = CONTENT.get(key, b"")
    if start is not None:
        data = data[start : (end + 1 if end is not None else None)]
    return {"Body": io.BytesIO(data), "ContentLength": len(data)}


def fixed_get_object(payload: bytes) -> Callable[..., dict]:
    """Erzeugt ein ``content.get_object``-Fake mit festem Inhalt (ohne Range)."""
    return lambda key, start=None, end=None: {
        "Body": io.BytesIO(payload),
        "ContentLength": len(payload),
    }


def range_get_object(payload: bytes) -> Callable[..., dict]:
    """Erzeugt ein ``content.get_object``-Fake mit festem Inhalt und Range-Support."""

    def _get_object(key: str, start: int | None = None, end: int | None = None) -> dict:
        if start is not None or end is not None:
            data = payload[start or 0 : (end + 1 if end is not None else None)]
        else:
            data = payload
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    return _get_object
