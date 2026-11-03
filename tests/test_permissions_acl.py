"""Entry-ACL-Endpunkte und minimierter Principal-Picker."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main

from .helpers import login


def _make_user(client: TestClient, admin: dict, username: str) -> tuple[str, dict]:
    user_id = client.post(
        "/api/users", headers=admin, json={"username": username, "password": "geheim123"}
    ).json()["id"]
    headers = {"Authorization": f"Bearer {login(client, username, 'geheim123')}"}
    return user_id, headers


def test_eigentuemer_verwaltet_acls_ohne_systemadmin():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _make_user(client, admin, "acl-alice")
        bob_id, bob = _make_user(client, admin, "acl-bob")

        folder = client.post("/api/folders", headers=alice, json={"name": "team"}).json()

        # Normaler Eigentümer (kein Systemadmin) darf ACLs lesen und setzen.
        assert client.get(f"/api/entries/{folder['id']}/acl", headers=alice).status_code == 200
        principals = client.get(
            f"/api/principals?entry_id={folder['id']}&q=acl-bob", headers=alice
        )
        assert principals.status_code == 200, principals.text
        picked = principals.json()
        assert any(item["id"] == bob_id and item["type"] == "user" for item in picked)
        # Minimierte Daten: keine E-Mail, kein Adminflag.
        for item in picked:
            assert set(item) == {"id", "type", "name", "display_name"}

        created = client.put(
            f"/api/entries/{folder['id']}/acl",
            headers=alice,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "permissions": ["read", "write", "publish"],
            },
        )
        assert created.status_code == 200, created.text
        acl = created.json()
        assert acl["perms"] == 1 | 2 | 32
        assert set(acl["permission_names"]) == {"read", "write", "publish"}

        listed = client.get(f"/api/entries/{folder['id']}/acl", headers=alice).json()
        assert [item["id"] for item in listed] == [acl["id"]]
        assert listed[0]["principal_name"] == "acl-bob"

        # Bob sieht die Datei/den Ordner und exakt diese Rechte.
        entry = client.get(f"/api/entries/{folder['id']}", headers=bob).json()
        assert set(entry["permissions"]) == {"read", "write", "publish"}
        assert "admin" not in entry["permissions"]

        # Entfernen -> kein Zugriff mehr.
        assert client.delete(
            f"/api/entries/{folder['id']}/acl/{acl['id']}", headers=alice
        ).status_code == 204
        assert client.get(f"/api/entries/{folder['id']}", headers=bob).status_code == 403
        assert client.get(f"/api/entries/{folder['id']}/acl", headers=alice).json() == []


def test_acls_sind_upsert_und_nicht_eskalierbar():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _make_user(client, admin, "acl-upsert-alice")
        bob_id, _bob = _make_user(client, admin, "acl-upsert-bob")
        folder = client.post("/api/folders", headers=alice, json={"name": "up"}).json()

        # POST vereinigt Bits; wiederholte Vergabe erzeugt keine Duplikate.
        for perms in (["read"], ["share"], ["publish"]):
            response = client.post(
                f"/api/entries/{folder['id']}/acl",
                headers=alice,
                json={"principal_type": "user", "principal_id": bob_id, "perms": perms},
            )
            assert response.status_code == 201, response.text
        listed = client.get(f"/api/entries/{folder['id']}/acl", headers=alice).json()
        assert len(listed) == 1
        assert set(listed[0]["permission_names"]) == {"read", "share", "publish"}

        # Systemrechte sind in einer ACL illegal.
        bad = client.post(
            f"/api/entries/{folder['id']}/acl",
            headers=alice,
            json={"principal_type": "user", "principal_id": bob_id, "perms": ["audit"]},
        )
        assert bad.status_code == 400

        # Fremder Nutzer und Systemadmin ohne Entry-Admin duerfen nichts aendern.
        stranger = _make_user(client, admin, "acl-stranger")[1]
        assert client.get(
            f"/api/entries/{folder['id']}/acl", headers=stranger
        ).status_code == 403
        assert client.put(
            f"/api/entries/{folder['id']}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "permissions": ["admin"],
            },
        ).status_code == 403
        assert client.get(
            f"/api/principals?entry_id={folder['id']}", headers=admin
        ).status_code == 403


def test_acl_audit_eintraege():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _make_user(client, admin, "acl-audit-alice")
        bob_id, _bob = _make_user(client, admin, "acl-audit-bob")
        folder = client.post("/api/folders", headers=alice, json={"name": "audit"}).json()

        acl = client.put(
            f"/api/entries/{folder['id']}/acl",
            headers=alice,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "permissions": ["read"],
            },
        ).json()
        client.delete(f"/api/entries/{folder['id']}/acl/{acl['id']}", headers=alice)

        set_rows = client.get("/api/audit?action=acl.set", headers=admin).json()
        del_rows = client.get("/api/audit?action=acl.delete", headers=admin).json()
        assert any(row["target_entry"] == folder["id"] for row in set_rows)
        assert any(row["target_entry"] == folder["id"] for row in del_rows)


def test_geteilt_uebersicht_zeigt_ausgehende_acls():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _make_user(client, admin, "out-alice")
        bob_id, bob = _make_user(client, admin, "out-bob")
        group_id = client.post(
            "/api/groups", headers=admin, json={"name": "out-group"}
        ).json()["id"]
        folder = client.post(
            "/api/folders", headers=alice, json={"name": "geteilt"}
        ).json()

        assert client.put(
            f"/api/entries/{folder['id']}/acl",
            headers=alice,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "permissions": ["read", "publish"],
            },
        ).status_code == 200
        assert client.put(
            f"/api/entries/{folder['id']}/acl",
            headers=alice,
            json={
                "principal_type": "group",
                "principal_id": group_id,
                "permissions": ["read"],
            },
        ).status_code == 200

        grants = client.get("/api/shared/outgoing", headers=alice).json()
        assert len(grants) == 2
        by_type = {g["principal_type"]: g for g in grants}
        assert by_type["user"]["principal_name"] == "out-bob"
        assert set(by_type["user"]["permission_names"]) == {"read", "publish"}
        assert by_type["user"]["entry_path"] == "/geteilt"
        assert by_type["group"]["principal_name"] == "out-group"

        # Empfänger und Systemadmin sehen die ausgehende Übersicht nicht.
        assert client.get("/api/shared/outgoing", headers=bob).json() == []
        assert client.get("/api/shared/outgoing", headers=admin).json() == []

        # Papierkorb-Einträge erscheinen nicht mehr.
        assert client.delete(f"/api/entries/{folder['id']}", headers=alice).status_code == 204
        assert client.get("/api/shared/outgoing", headers=alice).json() == []
