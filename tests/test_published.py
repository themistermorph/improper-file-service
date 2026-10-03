"""Tests für öffentliche Veröffentlichungen (Galerie unter /published)."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content
from ifs.models import Blob, BlobStatus

PAYLOAD = b"published-content"


def _fake_store_blob(db, local_path, mime=None):
    import hashlib

    data = open(local_path, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    blob = Blob(
        storage_key=f"cas/{sha[:2]}/{sha}", sha256=sha, size=len(data), status=BlobStatus.ready
    )
    db.add(blob)
    db.flush()
    return blob


def _fake_get_object(key, start=None, end=None):
    return {"Body": io.BytesIO(PAYLOAD), "ContentLength": len(PAYLOAD)}


def _login(client, username, password):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]


def _upload(client, headers, name="f.txt"):
    folder = client.post("/api/folders", headers=headers, json={"name": "pub"}).json()["id"]
    return client.put(
        f"/api/uploads/simple?parent_id={folder}&name={name}",
        headers=headers,
        content=PAYLOAD,
    ).json()


def test_publish_public_gallery_and_download(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        entry = _upload(client, admin)

        # Vor dem Veröffentlichen ist die Galerie leer und ein Direktaufruf 404.
        assert client.get("/api/published").json() == []
        assert client.get(f"/api/published/{entry['id']}/content").status_code == 404

        created = client.post(f"/api/published/{entry['id']}", headers=admin)
        assert created.status_code == 201
        assert created.json()["name"] == "f.txt"
        assert created.json()["published_by_username"] == "admin"

        # Öffentliche Liste ist ohne Anmeldung erreichbar.
        gallery = client.get("/api/published").json()
        assert [item["entry_id"] for item in gallery] == [entry["id"]]
        assert "path" not in gallery[0]

        # Download ohne Anmeldung.
        download = client.get(f"/api/published/{entry['id']}/content")
        assert download.status_code == 200
        assert download.content == PAYLOAD

        # Verwaltungsansicht listet die eigene Veröffentlichung mit Pfad.
        mine = client.get("/api/published/mine", headers=admin).json()
        assert mine[0]["entry_id"] == entry["id"]
        assert mine[0]["path"] == "/pub/f.txt"

        # Die echte Galerie-Seite wird ausgeliefert.
        page = client.get("/published")
        assert page.status_code == 200
        assert "Veröffentlichte Dateien" in page.text


def test_main_page_links_to_gallery():
    """Die Hauptseite muss einen Button zur öffentlichen Galerie enthalten."""
    with TestClient(main.app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'href="/published"' in page.text


def test_shares_nav_item_next_to_publications():
    """Freigaben stehen in der Navigation direkt neben den Veröffentlichungen."""
    with TestClient(main.app) as client:
        page = client.get("/").text
    assert 'id="nav-published"' in page
    assert 'id="nav-shares"' in page
    assert page.index('id="nav-published"') < page.index('id="nav-shares"')
    # Nicht mehr zusätzlich im Kontomenü: genau eine Verwendung.
    assert page.count("showView('shares')") == 1


def test_gallery_page_defines_hidden_helper():
    """Regression: Ohne .hidden bleibt die Lade-/Statuszeile sichtbar."""
    with TestClient(main.app) as client:
        page = client.get("/published")
        assert page.status_code == 200
        assert ".hidden { display: none !important; }" in page.text


def test_unpublish_removes_from_gallery(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        entry = _upload(client, admin)
        client.post(f"/api/published/{entry['id']}", headers=admin)

        assert client.delete(f"/api/published/{entry['id']}", headers=admin).status_code == 204
        assert client.get("/api/published").json() == []
        assert client.get(f"/api/published/{entry['id']}/content").status_code == 404
        # Erneutes Zurückziehen -> 404.
        assert client.delete(f"/api/published/{entry['id']}", headers=admin).status_code == 404


def test_publish_requires_permission(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        entry = _upload(client, admin)

        client.post(
            "/api/users", headers=admin, json={"username": "sam", "password": "geheim123"}
        )
        sam = {"Authorization": f"Bearer {_login(client, 'sam', 'geheim123')}"}

        # Fremde Datei: kein `read`/`share` -> 403.
        assert client.post(f"/api/published/{entry['id']}", headers=sam).status_code == 403


def test_publish_folder_serves_zip(monkeypatch):
    import zipfile

    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        parent_id = client.post(
            "/api/folders", headers=admin, json={"name": "pub"}
        ).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={parent_id}&name=f.txt",
            headers=admin,
            content=PAYLOAD,
        )

        created = client.post(f"/api/published/{parent_id}", headers=admin)
        assert created.status_code == 201
        assert created.json()["type"] == "folder"

        gallery = client.get("/api/published").json()
        folder_item = next(item for item in gallery if item["entry_id"] == parent_id)
        assert folder_item["type"] == "folder"

        download = client.get(f"/api/published/{parent_id}/content")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            names = archive.namelist()
        assert "pub/f.txt" in names

        assert client.delete(f"/api/published/{parent_id}", headers=admin).status_code == 204


def test_trash_revokes_publication(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        entry = _upload(client, admin)
        client.post(f"/api/published/{entry['id']}", headers=admin)
        assert len(client.get("/api/published").json()) == 1

        # In den Papierkorb -> Veröffentlichung wird zurückgezogen.
        assert client.delete(f"/api/entries/{entry['id']}", headers=admin).status_code == 204
        assert client.get("/api/published").json() == []
        assert client.get(f"/api/published/{entry['id']}/content").status_code == 404


def test_deactivate_revokes_publication(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        user = client.post(
            "/api/users", headers=admin, json={"username": "sam", "password": "geheim123"}
        ).json()
        sam = {"Authorization": f"Bearer {_login(client, 'sam', 'geheim123')}"}
        entry = _upload(client, sam)
        client.post(f"/api/published/{entry['id']}", headers=sam)
        assert len(client.get("/api/published").json()) == 1

        # Deaktivieren -> öffentliche Datei verschwindet.
        assert client.post(
            f"/api/users/{user['id']}/deactivate", headers=admin
        ).status_code in (200, 204)
        assert client.get("/api/published").json() == []


def test_mine_isolated_between_users(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        entry = _upload(client, admin)
        client.post(f"/api/published/{entry['id']}", headers=admin)

        client.post(
            "/api/users", headers=admin, json={"username": "sam", "password": "geheim123"}
        )
        sam = {"Authorization": f"Bearer {_login(client, 'sam', 'geheim123')}"}
        assert client.get("/api/published/mine", headers=sam).json() == []
        # Admin sieht alle Veröffentlichungen.
        assert len(client.get("/api/published/mine", headers=admin).json()) == 1
