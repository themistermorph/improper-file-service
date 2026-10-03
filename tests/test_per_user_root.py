"""Tests für per-user Wurzeln: Isolation, „Mit mir geteilt", Admin ohne Dateizugriff."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import namespace

from .conftest import make_user
from .helpers import login


def _auth(client, username, password="geheim123") -> dict:
    return {"Authorization": f"Bearer {login(client, username, password)}"}


def _create_user(client, admin, username) -> str:
    response = client.post(
        "/api/users", headers=admin, json={"username": username, "password": "geheim123"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_roots_are_per_user(db):
    first = make_user(db, "first")
    second = make_user(db, "second")
    root_first = namespace.ensure_root(db, first.id)
    root_second = namespace.ensure_root(db, second.id)

    assert root_first.id != root_second.id
    assert namespace.ensure_root(db, first.id).id == root_first.id
    assert namespace.path_of(db, root_first) == "/"


def test_api_isolation_between_users():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _create_user(client, admin, "alice-iso")
        _create_user(client, admin, "bob-iso")
        alice = _auth(client, "alice-iso")
        bob = _auth(client, "bob-iso")

        alice_root = client.get("/api/root", headers=alice).json()["id"]
        folder = client.post(
            "/api/folders", headers=alice, json={"name": "secret"}
        ).json()["id"]

        # Bob sieht nur seinen eigenen (leeren) Baum.
        assert client.get("/api/entries", headers=bob).json() == []
        assert (
            client.get(f"/api/entries?parent_id={alice_root}", headers=bob).status_code == 403
        )
        assert client.get(f"/api/entries/{folder}", headers=bob).status_code == 403

        # Auch der Admin hat keinen Dateizugriff auf fremde Wurzeln.
        assert (
            client.get(f"/api/entries?parent_id={alice_root}", headers=admin).status_code == 403
        )
        assert client.get(f"/api/entries/{folder}", headers=admin).status_code == 403

        # Alice sieht ihren Ordner.
        names = [e["name"] for e in client.get("/api/entries", headers=alice).json()]
        assert names == ["secret"]


def test_shared_with_me():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob_id = _create_user(client, admin, "bob-share")
        _create_user(client, admin, "alice-share")
        alice = _auth(client, "alice-share")
        bob = _auth(client, "bob-share")

        folder = client.post("/api/folders", headers=alice, json={"name": "team"}).json()["id"]

        # Ohne Freigabe sieht Bob nichts.
        assert client.get("/api/shared", headers=bob).json() == []

        granted = client.post(
            f"/api/entries/{folder}/acl",
            headers=alice,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "perms": ["read", "write"],
            },
        )
        assert granted.status_code == 201, granted.text

        shared = client.get("/api/shared", headers=bob).json()
        assert [e["name"] for e in shared] == ["team"]
        # Bob kann den geteilten Ordner öffnen.
        assert client.get(f"/api/entries?parent_id={folder}", headers=bob).status_code == 200


def test_bulk_user_delete_merges_into_target_root():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        carol_id = _create_user(client, admin, "carol-merge")
        target_id = _create_user(client, admin, "dave-merge")
        carol = _auth(client, "carol-merge")
        client.post("/api/folders", headers=carol, json={"name": "docs"})

        result = client.post(
            "/api/users/bulk/delete",
            headers=admin,
            json={"ids": [carol_id], "transfer_to": target_id},
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"ok": 1, "failed": []}

        dave = _auth(client, "dave-merge")
        dave_root = client.get("/api/root", headers=dave).json()["id"]
        children = client.get(f"/api/entries?parent_id={dave_root}", headers=dave).json()
        assert [e["name"] for e in children] == ["carol-merge"]
        moved = client.get(
            f"/api/entries?parent_id={children[0]['id']}", headers=dave
        ).json()
        assert [e["name"] for e in moved] == ["docs"]
