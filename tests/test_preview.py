"""Tests für die Datei-Vorschau (Token + Stream)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_store_blob, range_get_object

_fake_get_object = range_get_object(b"# Titel\nInhalt\n")


def test_preview_flow(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        folder = client.post("/api/folders", headers=headers, json={"name": "docs"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=readme.md",
            headers=headers,
            content=b"# Titel\nInhalt\n",
        ).json()

        # MIME wird aus dem Dateinamen abgeleitet
        detail = client.get(f"/api/entries/{entry['id']}", headers=headers).json()
        assert detail["mime"] == "text/markdown"

        # Token erzeugen
        created = client.post(f"/api/entries/{entry['id']}/preview-token", headers=headers)
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["url"].startswith("/api/preview/")
        assert payload["name"] == "readme.md"

        # Direkter Abruf ohne Authorization-Header (wie <img>/<video>)
        preview = client.get(payload["url"])
        assert preview.status_code == 200
        assert preview.content == b"# Titel\nInhalt\n"
        assert preview.headers["accept-ranges"] == "bytes"

        # Range funktioniert (Video-Seeking)
        partial = client.get(payload["url"], headers={"Range": "bytes=0-6"})
        assert partial.status_code == 206
        assert partial.content == b"# Titel"

        # Ungültiger Token
        assert client.get("/api/preview/ungueltig").status_code == 401

        # Ordner hat keinen Dateiinhalt -> 400
        assert client.post(
            f"/api/entries/{folder}/preview-token", headers=headers
        ).status_code == 400

        # Papierkorb-Datei ist nicht abrufbar
        client.delete(f"/api/entries/{entry['id']}", headers=headers)
        assert client.get(payload["url"]).status_code == 404
