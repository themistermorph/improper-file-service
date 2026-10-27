"""Tests für benutzerdefinierte Veröffentlichungs-Links (/published/<user>/<suffix>)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_store_blob, fixed_get_object, login

PAYLOAD = b"published-content"
_fake_get_object = fixed_get_object(PAYLOAD)


def _folder(client, headers, name="pub"):
    return client.post("/api/folders", headers=headers, json={"name": name}).json()["id"]


def _file(client, headers, folder_id, name, payload=PAYLOAD):
    return client.put(
        f"/api/uploads/simple?parent_id={folder_id}&name={name}",
        headers=headers,
        content=payload,
    ).json()


def _publish(client, headers, entry_id, **body):
    return client.post(f"/api/published/{entry_id}", headers=headers, json=body)


def _patch(client, headers, entry_id, body):
    return client.patch(f"/api/published/{entry_id}", headers=headers, json=body)


def _redirect(client, path):
    return client.get(path, follow_redirects=False)


def _admin(client):
    return {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}


def test_public_path_redirect_case_insensitive(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = _admin(client)
        folder_id = _folder(client, admin)
        entry = _file(client, admin, folder_id, "f.txt")

        created = _publish(client, admin, entry["id"], public_suffix="bericht")
        assert created.status_code == 201
        data = created.json()
        assert data["public_path"] == "admin/bericht"
        assert data["public_suffix"] == "bericht"
        assert data["public_name"] is None

        gallery = client.get("/api/published").json()
        assert gallery[0]["public_path"] == "admin/bericht"

        response = _redirect(client, "/published/admin/bericht")
        assert response.status_code == 307
        assert response.headers["location"].endswith(
            f"/api/published/{entry['id']}/content"
        )
        assert response.headers["cache-control"] == "no-store"

        # Case-insensitiv, auch mit abschließendem Slash.
        assert _redirect(client, "/published/ADMIN/BERICHT").status_code == 307
        assert _redirect(client, "/published/admin/bericht/").status_code == 307

        # Unbekannt bzw. zu viele Segmente.
        assert _redirect(client, "/published/admin/unbekannt").status_code == 404
        assert _redirect(client, "/published/admin/a/b").status_code == 404

        # Galerie-Seite bleibt erreichbar; kanonische API-URL bleibt gültig.
        assert client.get("/published").status_code == 200
        assert client.get("/published/").status_code == 200
        assert client.get(f"/api/published/{entry['id']}/content").status_code == 200


def test_public_suffix_validation_and_uniqueness(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = _admin(client)
        folder_id = _folder(client, admin)
        first = _file(client, admin, folder_id, "a.txt")
        # Anderer Inhalt: der Test-Fake legt je Upload eine Blob-Zeile an.
        second = _file(client, admin, folder_id, "b.txt", b"second-content")

        for bad in ("a/b", "a b", "..", ".", "x?y", "x#y", "x%y"):
            response = _publish(client, admin, first["id"], public_suffix=bad)
            assert response.status_code == 400, (bad, response.text)

        assert _publish(client, admin, first["id"], public_suffix="X").status_code == 201
        # Case-insensitiv belegt.
        duplicate = _publish(client, admin, second["id"], public_suffix="x")
        assert duplicate.status_code == 409

        # Nach dem Zurückziehen ist der Suffix wieder frei.
        assert client.delete(f"/api/published/{first['id']}", headers=admin).status_code == 204
        assert _publish(client, admin, second["id"], public_suffix="x").status_code == 201
        assert _redirect(client, "/published/admin/x").status_code == 307


def test_public_suffix_patch_keeps_other_field(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = _admin(client)
        folder_id = _folder(client, admin)
        entry = _file(client, admin, folder_id, "f.txt")

        created = _publish(
            client, admin, entry["id"],
            public_name="Anzeige", public_suffix="alt",
        )
        assert created.json()["public_path"] == "admin/alt"

        patched = _patch(client, admin, entry["id"], {"public_name": "Neu"})
        assert patched.status_code == 200
        assert patched.json()["public_name"] == "Neu"
        assert patched.json()["public_path"] == "admin/alt"

        patched = _patch(client, admin, entry["id"], {"public_suffix": "neu"})
        assert patched.json()["public_path"] == "admin/neu"
        assert patched.json()["public_name"] == "Neu"

        cleared = _patch(client, admin, entry["id"], {"public_suffix": None})
        assert cleared.json()["public_path"] is None
        assert _redirect(client, "/published/admin/neu").status_code == 404

        cleared_name = _patch(client, admin, entry["id"], {"public_name": None})
        assert cleared_name.json()["public_name"] is None


def test_public_suffix_folder_and_republish(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = _admin(client)
        folder_id = _folder(client, admin, "ordner")
        created = _publish(client, admin, folder_id, public_suffix="projekt")
        assert created.status_code == 201
        assert created.json()["public_path"] == "admin/projekt"
        assert _redirect(client, "/published/admin/projekt").status_code == 307

        # Idempotentes Re-Publish ohne Payload behält den Suffix.
        reposted = _publish(client, admin, folder_id)
        assert reposted.json()["public_path"] == "admin/projekt"
        # Mit neuem Suffix wird er aktualisiert.
        updated = _publish(client, admin, folder_id, public_suffix="archiv")
        assert updated.json()["public_path"] == "admin/archiv"
        assert _redirect(client, "/published/admin/projekt").status_code == 404


def test_publish_ui_contains_suffix_field():
    with TestClient(main.app) as client:
        page = client.get("/").text
        gallery = client.get("/published").text
    assert "Eigener Link-Suffix" in page
    assert "public_suffix" in page
    assert "public_path" in gallery
