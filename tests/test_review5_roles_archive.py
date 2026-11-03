"""Regressionstests für die Findings aus dem fünften Review.

- Rollen: Systemadmin darf fremde, entry-gebundene Zuweisungen nicht löschen.
- Archive-Jobs: Abbrechen während des Builds darf keine ZIP-Leiche hinterlassen.
- Archive: Ausweichnamen müssen auch gegen bereits vergebene Ausweichnamen eindeutig sein.
- Admin: ACL-Vergaben werden auditiert.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import zipfile

from fastapi.testclient import TestClient

from ifs import main
from ifs.config import get_settings
from ifs.core import archive, archive_jobs, namespace

from .conftest import make_user
from .helpers import login


def _auth(client, username: str, password: str = "geheim123") -> dict:
    return {"Authorization": f"Bearer {login(client, username, password)}"}


def _create_user(client, admin, username: str) -> str:
    response = client.post(
        "/api/users", headers=admin, json={"username": username, "password": "geheim123"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- Finding 1: Systemadmin kann fremde Entry-ACLs nicht löschen -------------


def test_system_admin_cannot_delete_foreign_entry_acl():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _create_user(client, admin, "alice-r5")
        bob_id = _create_user(client, admin, "bob-r5")
        alice = _auth(client, "alice-r5")

        folder = client.post(
            "/api/folders", headers=alice, json={"name": "team-r5"}
        ).json()["id"]
        granted = client.post(
            f"/api/entries/{folder}/acl",
            headers=alice,
            json={"principal_type": "user", "principal_id": bob_id, "perms": ["read"]},
        )
        assert granted.status_code == 201, granted.text
        acl_id = granted.json()["id"]

        # Systemadmin ohne `admin` am Eintrag darf den fremden Grant nicht löschen.
        denied = client.delete(f"/api/entries/{folder}/acl/{acl_id}", headers=admin)
        assert denied.status_code == 403, denied.text

        # Der Eintrags-Admin (Besitzer) kann ihn weiterhin entfernen.
        removed = client.delete(
            f"/api/entries/{folder}/acl/{acl_id}", headers=alice
        )
        assert removed.status_code == 204, removed.text


# --- Finding 2: abgebrochener Job darf keine ZIP-Datei zurücklassen -----------


def test_cancelled_job_running_build_removes_zip(monkeypatch):
    spool = get_settings().upload_spool_dir
    os.makedirs(spool, exist_ok=True)
    token = "review5-cancel-token"
    created: list[str] = []
    built = threading.Event()

    def fake_build_zip(db, entries, progress=None):
        fd, path = tempfile.mkstemp(prefix="ifs-zip-review5-", suffix=".zip", dir=spool)
        os.close(fd)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("x.txt", b"x")
        created.append(path)
        # Job genau während des Builds abbrechen: _run findet danach keinen Job mehr.
        assert archive_jobs.cancel(token) is True
        built.set()
        return path

    monkeypatch.setattr(archive, "build_zip", fake_build_zip)
    archive_jobs.start(token, [], "leiche.zip", 0, 0)

    assert built.wait(timeout=5), "Build wurde nicht gestartet"
    deadline = time.time() + 5
    while created and os.path.exists(created[0]) and time.time() < deadline:
        time.sleep(0.02)

    assert created, "Fake hat keine ZIP-Datei erzeugt"
    assert not os.path.exists(created[0]), "ZIP-Leiche blieb im Spool zurück"
    assert archive_jobs.get(token) is None


# --- Finding 3: ZIP-Ausweichnamen kollidieren nicht ---------------------------


def test_build_zip_unique_prefixes_for_equal_and_similar_names(db):
    owner = make_user(db, "zip-owner-r5")
    root = namespace.ensure_root(db, owner.id)
    first_parent = namespace.create_folder(db, root, "p1", owner.id)
    second_parent = namespace.create_folder(db, root, "p2", owner.id)
    first = namespace.create_folder(db, first_parent, "a", owner.id)
    second = namespace.create_folder(db, second_parent, "a", owner.id)
    third = namespace.create_folder(db, second_parent, "a (2)", owner.id)

    path = archive.build_zip(db, [first, second, third])
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        assert len(names) == len(set(names)), names
        assert set(names) == {"a/", "a (2)/", "a (2) (2)/"}
    finally:
        archive.remove_zip(path)


# --- Finding 4: ACL-Vergabe landet im Audit-Log -------------------------------


def test_acl_set_is_audited():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        target_id = _create_user(client, admin, "target-r5")
        folder = client.post(
            "/api/folders", headers=admin, json={"name": "acl-r5"}
        ).json()["id"]

        granted = client.post(
            f"/api/entries/{folder}/acl",
            headers=admin,
            json={"principal_type": "user", "principal_id": target_id, "perms": ["read"]},
        )
        assert granted.status_code == 201, granted.text

        events = client.get("/api/audit?action=acl.set", headers=admin).json()
        assert events, "Kein acl.set-Audit-Eintrag vorhanden"
        event = events[0]
        assert event["action"] == "acl.set"
        assert event["target_entry"] == folder
        assert event["details"]["principal"] == target_id
        assert event["details"]["perms"] == ["read"]
