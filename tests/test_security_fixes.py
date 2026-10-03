"""Regressionstests für die Findings M1 (copy/share) und M2 (Share-Upload-Limits)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from ifs import main
from ifs.api import deps as deps_module
from ifs.core import content, ratelimit
from ifs.db import session_scope
from ifs.models import RoleAssignment

from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"secret")


def _make_readonly_reader(client, admin) -> tuple[dict, str, str]:
    """Legt bob an, gibt read auf 'conf' und read+write auf 'team'.

    Liefert (bob-headers, secret-id, target-folder-id), wobei ``target`` ein von Bob
    selbst angelegter Unterordner ist (Owner -> inkl. share).
    """
    bob_id = client.post(
        "/api/users", headers=admin, json={"username": "bob", "password": "geheim123"}
    ).json()["id"]
    bob = {"Authorization": f"Bearer {login(client, 'bob', 'geheim123')}"}

    conf = client.post("/api/folders", headers=admin, json={"name": "conf"}).json()["id"]
    team = client.post("/api/folders", headers=admin, json={"name": "team"}).json()["id"]
    client.post(
        f"/api/entries/{conf}/acl",
        headers=admin,
        json={"principal_type": "user", "principal_id": bob_id, "perms": ["read"]},
    )
    client.post(
        f"/api/entries/{team}/acl",
        headers=admin,
        json={"principal_type": "user", "principal_id": bob_id, "perms": ["read", "write"]},
    )
    secret = client.put(
        f"/api/uploads/simple?parent_id={conf}&name=secret.txt",
        headers=admin,
        content=b"secret",
    ).json()["id"]
    target = client.post(
        "/api/folders", headers=bob, json={"parent_id": team, "name": "bob-home"}
    ).json()["id"]
    return bob, secret, target


def test_m1_copy_requires_share_permission(monkeypatch):
    """Ohne `share` auf der Quelle ist weder direktes Teilen noch copy erlaubt."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob, secret, target = _make_readonly_reader(client, admin)

        # Direktes Teilen ist verboten.
        assert client.post("/api/shares", headers=bob, json={"entry_id": secret}).status_code == 403

        # Kopieren ist ebenfalls verboten -> kein Bypass mehr.
        copy = client.post(
            f"/api/entries/{secret}/copy",
            headers=bob,
            json={"parent_id": target, "name": "kopie.txt"},
        )
        assert copy.status_code == 403

        # Kein Klon entstanden.
        names = [
            e["name"]
            for e in client.get(f"/api/entries?parent_id={target}", headers=bob).json()
        ]
        assert "kopie.txt" not in names


def test_m1_copy_allowed_with_share_permission(monkeypatch):
    """Mit `share` auf der Quelle bleibt copy funktionsfähig."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob, secret, target = _make_readonly_reader(client, admin)

        # share nachtraeglich auf dem Quellordner gewaehren.
        bob_id = client.get("/api/users?q=bob", headers=admin).json()["items"][0]["id"]
        conf = client.get(f"/api/entries/{secret}", headers=admin).json()["parent_id"]
        assert (
            client.post(
                f"/api/entries/{conf}/acl",
                headers=admin,
                json={
                    "principal_type": "user",
                    "principal_id": bob_id,
                    "perms": ["read", "share"],
                },
            ).status_code
            == 201
        )

        copy = client.post(
            f"/api/entries/{secret}/copy",
            headers=bob,
            json={"parent_id": target, "name": "kopie.txt"},
        )
        assert copy.status_code == 201, copy.text
        clone = copy.json()
        assert clone["id"] != secret

        # Kopie gehoert Bob -> teilbar.
        share = client.post("/api/shares", headers=bob, json={"entry_id": clone["id"]})
        assert share.status_code == 201, share.text
        assert (
            client.get(f"/api/shares/{share.json()['token']}/content").status_code == 200
        )


def test_m2_share_upload_size_limit_declared(monkeypatch):
    """Zu grosse Share-Uploads werden anhand Content-Length frueh abgelehnt."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    monkeypatch.setattr(
        "ifs.api.shares.get_settings",
        lambda: SimpleNamespace(effective_share_upload_limit=5),
    )

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "drop"}).json()["id"]
        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": folder, "allow_upload": True}
        ).json()["token"]

        too_big = client.post(
            f"/api/shares/{token}/upload?name=big.txt", content=b"x" * 10
        )
        assert too_big.status_code == 413
        assert client.get(f"/api/entries?parent_id={folder}", headers=admin).json() == []

        ok = client.post(f"/api/shares/{token}/upload?name=ok.txt", content=b"12345")
        assert ok.status_code == 201, ok.text


def test_m2_share_upload_size_limit_streaming(monkeypatch):
    """Auch ohne Content-Length greift die Chunk-fuer-Chunk-Pruefung."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    monkeypatch.setattr(
        "ifs.api.shares.get_settings",
        lambda: SimpleNamespace(effective_share_upload_limit=5),
    )

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "drop2"}).json()["id"]
        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": folder, "allow_upload": True}
        ).json()["token"]

        def chunks():
            yield b"123"
            yield b"456"

        response = client.post(
            f"/api/shares/{token}/upload?name=chunked.txt", content=chunks()
        )
        assert response.status_code == 413, response.text
        assert client.get(f"/api/entries?parent_id={folder}", headers=admin).json() == []


def test_m2_share_upload_rate_limited(monkeypatch):
    """Der oeffentliche Upload-Endpunkt ist pro Freigabe+IP rate-limitiert."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    ratelimit.install_share_limiter(ratelimit.FixedWindowLimiter(2, 300))

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "flood"}).json()["id"]
        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": folder, "allow_upload": True}
        ).json()["token"]

        assert client.post(f"/api/shares/{token}/upload?name=a.txt", content=b"a").status_code == 201
        assert client.post(f"/api/shares/{token}/upload?name=b.txt", content=b"b").status_code == 201

        blocked = client.post(f"/api/shares/{token}/upload?name=c.txt", content=b"c")
        assert blocked.status_code == 429
        assert blocked.headers.get("retry-after")


def test_m2_share_upload_limit_property():
    from ifs.config import Settings

    cfg = Settings(
        jwt_secret="x" * 40,
        max_upload_size=5,
        share_upload_max_size=100,
    )
    # Es gilt das kleinere der beiden Limits.
    assert cfg.effective_share_upload_limit == 5

    cfg2 = Settings(jwt_secret="x" * 40, max_upload_size=0, share_upload_max_size=100)
    assert cfg2.effective_share_upload_limit == 100

    cfg3 = Settings(jwt_secret="x" * 40, max_upload_size=0, share_upload_max_size=0)
    assert cfg3.effective_share_upload_limit == 0


# --- N1: Archiv-Job-Endpunkte prüfen den Token ---------------------------------


def test_n1_archive_job_requires_valid_token(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "job"}).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt", headers=auth, content=b"data"
        )
        job = client.post("/api/archive/jobs", headers=auth, json={"entry_ids": [folder]})
        assert job.status_code == 201, job.text
        token = job.json()["token"]

        assert client.get(f"/api/archive/jobs/{token}").status_code == 200
        assert client.get("/api/archive/jobs/kein-token").status_code == 401

        # Passwortänderung -> scoped Token sofort ungültig.
        assert (
            client.post(
                "/api/me/password",
                headers=auth,
                json={"current_password": "admin", "new_password": "neuespass9"},
            ).status_code
            == 204
        )
        assert client.get(f"/api/archive/jobs/{token}").status_code == 401
        assert client.delete(f"/api/archive/jobs/{token}").status_code == 401


# --- N2: Share-Metadaten nur mit Passwort --------------------------------------


def test_n2_share_metadata_requires_password(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "meta"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt", headers=auth, content=b"data"
        ).json()
        token = client.post(
            "/api/shares", headers=auth, json={"entry_id": entry["id"], "password": "pw"}
        ).json()["token"]

        assert client.get(f"/api/shares/{token}").status_code == 401
        ok = client.get(f"/api/shares/{token}", headers={"X-Share-Password": "pw"})
        assert ok.status_code == 200, ok.text
        assert ok.headers.get("cache-control") == "no-store"


# --- N3: Limiter begrenzen ihren Speicher --------------------------------------


def test_n3_login_limiter_prunes_entries(monkeypatch):
    limiter = ratelimit.LoginRateLimiter(5, 60, 30, 60, max_entries=50)
    now = {"t": 1000.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now["t"])

    for index in range(200):
        limiter.record_failure(f"key-{index}")
    assert len(limiter._entries) <= 50

    # Nach Ablauf des Fensters werden alte Einträge bevorzugt entfernt.
    now["t"] += 1000
    limiter.record_failure("fresh")
    assert len(limiter._entries) <= 51


def test_n3_share_limiter_prunes_entries(monkeypatch):
    limiter = ratelimit.FixedWindowLimiter(10, 60, max_entries=50)
    now = {"t": 1000.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now["t"])

    for index in range(200):
        limiter.allow(f"key-{index}")
    assert len(limiter._buckets) <= 50


# --- N4: Produktion lehnt Default-DB-Zugangsdaten ab ---------------------------


def test_n4_prod_rejects_default_db_credentials():
    from pydantic import ValidationError

    from ifs.config import Settings

    strong = {
        "environment": "prod",
        "jwt_secret": "x" * 40,
        "admin_password": "strongpass",
        "s3_secret_key": "s3secret",
    }
    for url in (
        "postgresql+psycopg://ifs:ifs@db:5432/ifs",
        "postgresql+psycopg://postgres:postgres@db:5432/ifs",
    ):
        try:
            Settings(database_url=url, **strong)
        except ValidationError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"Default-DB-Zugangsdaten akzeptiert: {url}")

    # Ein eigenes Passwort ist erlaubt.
    assert Settings(
        database_url="postgresql+psycopg://ifs:geheim@db:5432/ifs", **strong
    ).is_production


# --- N5: HSTS hinter TLS-terminierendem Proxy ----------------------------------


def _request(scheme="http", peer="10.0.0.5", forwarded_proto=None) -> Request:
    headers = (
        [(b"x-forwarded-proto", forwarded_proto.encode())] if forwarded_proto else []
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("test", 80),
            "scheme": scheme,
        }
    )


def test_n5_is_secure_request_honors_forwarded_proto(monkeypatch):
    monkeypatch.setattr(
        deps_module, "get_settings", lambda: SimpleNamespace(trusted_proxies="10.0.0.0/8")
    )
    assert deps_module.is_secure_request(_request(scheme="https")) is True
    assert (
        deps_module.is_secure_request(_request(peer="10.0.0.5", forwarded_proto="https"))
        is True
    )
    assert (
        deps_module.is_secure_request(_request(peer="10.0.0.5", forwarded_proto="http"))
        is False
    )
    # Nicht vertrauenswürdiger Peer -> Header wird ignoriert.
    assert (
        deps_module.is_secure_request(_request(peer="192.168.1.9", forwarded_proto="https"))
        is False
    )
    assert deps_module.is_secure_request(_request(peer="10.0.0.5")) is False


# --- N7: delete_user räumt Rollenzuweisungen auf -------------------------------


def test_n7_delete_user_removes_role_assignments(monkeypatch):
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        user_id = client.post(
            "/api/users", headers=admin, json={"username": "roll", "password": "geheim123"}
        ).json()["id"]
        role_id = next(
            role["id"]
            for role in client.get("/api/roles?scope=system", headers=admin).json()
            if role["name"] == "user"
        )
        assigned = client.post(f"/api/users/{user_id}/roles/{role_id}", headers=admin)
        assert assigned.status_code == 201, assigned.text

        with session_scope() as db:
            remaining = db.execute(
                select(RoleAssignment).where(RoleAssignment.principal_id == UUID(user_id))
            ).scalars().all()
            assert remaining

        assert client.delete(f"/api/users/{user_id}", headers=admin).status_code == 204

        with session_scope() as db:
            remaining = db.execute(
                select(RoleAssignment).where(RoleAssignment.principal_id == UUID(user_id))
            ).scalars().all()
            assert remaining == []


# --- Fixes aus dem zweiten Review: keine verwaisten S3-Objekte ----------------


def test_conflict_does_not_touch_object_storage(monkeypatch):
    """Scheitert ein Upload an einem Namenskonflikt, darf vorher nichts nach S3 gehen."""
    def _bomb(db, local_path, mime=None):
        raise AssertionError("store_blob darf bei absehbarem Konflikt nicht aufgerufen werden")

    monkeypatch.setattr(content, "store_blob", _bomb)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        root = client.get("/api/root", headers=admin).json()["id"]
        created = client.post(
            "/api/folders",
            headers=admin,
            json={"name": "conflict-folder", "parent_id": root},
        )
        assert created.status_code == 201, created.text

        response = client.put(
            f"/api/uploads/simple?parent_id={root}&name=conflict-folder",
            headers=admin,
            content=b"payload",
        )
        assert response.status_code == 409, response.text


def test_sha_mismatch_does_not_touch_object_storage(monkeypatch):
    """Bei falscher Client-SHA wird nicht hochgeladen (kein verwaistes Objekt)."""
    def _bomb(db, local_path, mime=None):
        raise AssertionError("store_blob darf bei SHA-Fehler nicht aufgerufen werden")

    monkeypatch.setattr(content, "store_blob", _bomb)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        root = client.get("/api/root", headers=admin).json()["id"]
        session = client.post(
            "/api/uploads",
            headers=admin,
            json={"parent_id": root, "name": "sha.txt", "sha256": "0" * 64},
        ).json()
        patched = client.patch(
            f"/api/uploads/{session['id']}",
            headers={**admin, "Upload-Offset": "0"},
            content=b"abc",
        )
        assert patched.status_code == 204, patched.text

        done = client.post(f"/api/uploads/{session['id']}/complete", headers=admin)
        assert done.status_code == 400, done.text


def test_copy_counts_against_quota(monkeypatch):
    from ifs.core import quota as quota_core

    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    monkeypatch.setattr(
        quota_core, "get_settings", lambda: SimpleNamespace(default_quota_bytes=5)
    )

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "quota"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=b"12345",
        ).json()

        copy = client.post(
            f"/api/entries/{entry['id']}/copy",
            headers=admin,
            json={"parent_id": folder, "name": "kopie.txt"},
        )
        assert copy.status_code == 413, copy.text
