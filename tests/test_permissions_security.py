"""Sicherheitsmatrix der Berechtigungs-Umstellung.

Prüft die Trennung von Eintrags- und Systemrechten sowie die Isolation des
Systemadmins (kein Dateizugriff auf fremde Bäume).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main

from .helpers import login


def _user(client: TestClient, admin: dict, name: str) -> tuple[str, dict]:
    user_id = client.post(
        "/api/users", headers=admin, json={"username": name, "password": "geheim123"}
    ).json()["id"]
    headers = {"Authorization": f"Bearer {login(client, name, 'geheim123')}"}
    return user_id, headers


def _grant(client: TestClient, owner: dict, entry_id: str, principal_id: str, perms: list[str]):
    return client.put(
        f"/api/entries/{entry_id}/acl",
        headers=owner,
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            "permissions": perms,
        },
    )


def test_entry_admin_ist_kein_systemadmin():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _user(client, admin, "sec-alice")
        bob_id, bob = _user(client, admin, "sec-bob")
        folder = client.post("/api/folders", headers=alice, json={"name": "team"}).json()

        assert _grant(client, alice, folder["id"], bob_id, ["read", "admin"]).status_code == 200
        assert client.get(f"/api/entries/{folder['id']}/acl", headers=bob).status_code == 200
        # Eintrags-ADMIN bleibt auf den Eintrag begrenzt.
        assert client.get("/api/users", headers=bob).status_code == 403
        assert client.get("/api/audit", headers=bob).status_code == 403
        assert client.get("/api/system/stats", headers=bob).status_code == 403


def test_systemrechte_erlauben_keine_mutationen():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        user_id, user = _user(client, admin, "sec-monitor")
        assert client.put(
            f"/api/users/{user_id}/permissions",
            headers=admin,
            json={"permissions": ["audit", "monitor"]},
        ).status_code == 200
        assert client.get("/api/audit", headers=user).status_code == 200
        assert client.get("/api/system/stats", headers=user).status_code == 200

        assert client.post(
            "/api/users", headers=user, json={"username": "neu", "password": "geheim123"}
        ).status_code == 403
        assert client.put(
            f"/api/users/{user_id}/permissions",
            headers=user,
            json={"permissions": ["audit"]},
        ).status_code == 403
        assert client.post(
            "/api/roles", headers=user, json={"name": "x", "permissions": []}
        ).status_code == 403
        # System-Grant gewährt keinen Dateizugriff.
        _alice_id, alice = _user(client, admin, "sec-owner")
        folder = client.post("/api/folders", headers=alice, json={"name": "privat"}).json()
        assert client.get(f"/api/entries/{folder['id']}", headers=user).status_code == 403


def test_systemadmin_hat_keinen_dateizugriff():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _user(client, admin, "sec-isolated")
        folder = client.post("/api/folders", headers=alice, json={"name": "home"}).json()

        assert client.get(f"/api/entries/{folder['id']}", headers=admin).status_code == 403
        assert client.get(f"/api/entries/{folder['id']}/acl", headers=admin).status_code == 403
        # Benutzerverwaltung funktioniert weiterhin.
        assert client.get("/api/users", headers=admin).status_code == 200


def test_share_publish_admin_sind_getrennte_rechte():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _user(client, admin, "sec-alice2")
        bob_id, bob = _user(client, admin, "sec-bob2")
        folder = client.post("/api/folders", headers=alice, json={"name": "links"}).json()

        assert _grant(client, alice, folder["id"], bob_id, ["read", "share"]).status_code == 200
        assert client.post(
            "/api/shares", headers=bob, json={"entry_id": folder["id"]}
        ).status_code == 201
        assert client.post(f"/api/published/{folder['id']}", headers=bob).status_code == 403
        assert client.put(
            f"/api/entries/{folder['id']}/acl",
            headers=bob,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "permissions": ["admin"],
            },
        ).status_code == 403

        # publish hinzufügen: Galerie erlaubt, Zugriff weiterhin nicht.
        assert _grant(client, alice, folder["id"], bob_id, ["read", "share", "publish"]).status_code == 200
        assert client.post(f"/api/published/{folder['id']}", headers=bob).status_code == 201
        assert client.get(f"/api/entries/{folder['id']}/acl", headers=bob).status_code == 403

        # admin hinzufügen: Zugriff erlaubt, aber weiterhin kein Systemadmin.
        assert _grant(client, alice, folder["id"], bob_id, ["read", "share", "publish", "admin"]).status_code == 200
        assert client.get(f"/api/entries/{folder['id']}/acl", headers=bob).status_code == 200
        assert client.get("/api/users", headers=bob).status_code == 403

