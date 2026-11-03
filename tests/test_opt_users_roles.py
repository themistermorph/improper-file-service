"""Performance-Regressionstests für Benutzer- und Rollenlisten.

Die Tests zählen SQL-Statements über ``before_cursor_execute`` und stellen
sicher, dass die Listen-Endpunkte nicht linear mit der Anzahl von Benutzern,
Gruppen oder Zuweisungen wachsen (keine Lazy-Load-N+1). Zusätzlich werden
Antwortformat, Rechtebündel und Schutzregeln unverändert geprüft.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event

from ifs import main
from ifs.core import authz, namespace, roles
from ifs.db import get_engine, session_scope
from ifs.models import ACL, Group, PrincipalType, RoleScope

from .conftest import make_user
from .helpers import login


class _QueryCounter:
    """Zählt SQL-Statements und bei ``executemany`` die Parametersätze.

    Ein ORM-Flush bündelt gleichartige DELETEs in einem ``executemany``; ohne
    Gewichtung nach Parametersätzen bliebe lineare Arbeit unsichtbar.
    """

    def __init__(self) -> None:
        self.work = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        if executemany and parameters is not None:
            try:
                self.work += len(parameters)
                return
            except TypeError:
                pass
        self.work += 1


def _count_queries(call):
    """Führt ``call`` aus und liefert (Antwort, Anzahl DB-Operationen)."""
    counter = _QueryCounter()
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        response = call()
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    return response, counter.work


def _create_user(client: TestClient, admin: dict, username: str) -> str:
    response = client.post(
        "/api/users", headers=admin, json={"username": username, "password": "geheim123"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_group(client: TestClient, admin: dict, name: str) -> str:
    response = client.post("/api/groups", headers=admin, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _add_membership(client: TestClient, admin: dict, user_id: str, group_id: str) -> None:
    response = client.post(f"/api/users/{user_id}/groups/{group_id}", headers=admin)
    assert response.status_code == 204, response.text


def test_benutzerliste_abfragen_wachsen_nicht_linear():
    """GET /users löst keine Gruppen-Abfrage je Benutzer/Mitgliedschaft aus."""
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}

        # Referenzwert: leere Trefferliste (Auth + Zählung + Auswahl).
        empty, empty_count = _count_queries(
            lambda: client.get("/api/users?q=niemand", headers=admin)
        )
        assert empty.status_code == 200
        assert empty.json()["total"] == 0

        # Kleiner Bestand: 3 Benutzer in 2 Gruppen (je 2 Mitgliedschaften).
        small_ids = [
            _create_user(client, admin, f"opt-user-{index:02d}") for index in range(3)
        ]
        small_groups = [
            _create_group(client, admin, f"opt-gruppe-{index}") for index in range(2)
        ]
        for offset, user_id in enumerate(small_ids):
            for step in range(2):
                _add_membership(client, admin, user_id, small_groups[(offset + step) % 2])

        small, small_count = _count_queries(
            lambda: client.get("/api/users?limit=200", headers=admin)
        )
        assert small.status_code == 200
        body = small.json()
        assert body["total"] == 1 + len(small_ids)  # inkl. Seed-Admin
        names = [item["username"] for item in body["items"]]
        assert names == sorted(names)  # Reihenfolge (nach Benutzername) unverändert

        # Großer Bestand: 12 weitere Benutzer in 4 weiteren Gruppen.
        large_ids = [
            _create_user(client, admin, f"opt-user-{index:02d}") for index in range(3, 15)
        ]
        large_groups = [
            _create_group(client, admin, f"opt-gruppe-{index}") for index in range(2, 6)
        ]
        for offset, user_id in enumerate(large_ids):
            for step in range(2):
                _add_membership(client, admin, user_id, large_groups[(offset + step) % 4])

        large, large_count = _count_queries(
            lambda: client.get("/api/users?limit=200", headers=admin)
        )
        assert large.status_code == 200
        assert large.json()["total"] == 1 + len(small_ids) + len(large_ids)

        # Keine Abfrage je Benutzer/Gruppe: Anzahl bleibt konstant (wie leere Liste).
        assert small_count == empty_count
        assert large_count == empty_count


def test_benutzerdetail_gruppen_wachsen_nicht_linear():
    """GET /users/{id} lädt alle Gruppen in einer Sammelabfrage (kein N+1)."""
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        user_id = _create_user(client, admin, "opt-detail-user")
        groups = [
            _create_group(client, admin, f"opt-detail-gruppe-{index}") for index in range(4)
        ]
        _add_membership(client, admin, user_id, groups[0])

        first, first_count = _count_queries(
            lambda: client.get(f"/api/users/{user_id}", headers=admin)
        )
        assert first.status_code == 200
        assert [group["name"] for group in first.json()["groups"]] == [
            "opt-detail-gruppe-0"
        ]

        for group_id in groups[1:]:
            _add_membership(client, admin, user_id, group_id)

        second, second_count = _count_queries(
            lambda: client.get(f"/api/users/{user_id}", headers=admin)
        )
        assert second.status_code == 200
        names = sorted(group["name"] for group in second.json()["groups"])
        assert names == [f"opt-detail-gruppe-{index}" for index in range(4)]
        assert second_count == first_count  # eine Sammelabfrage, keine je Gruppe


def test_systemrollenliste_zeigt_nur_adminzuweisung():
    """GET /users/{id}/roles liefert nur noch Admin-Zuweisungen, konstant."""
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        user_id = _create_user(client, admin, "opt-rollen-user")
        folder_id = client.post(
            "/api/folders", headers=admin, json={"name": "opt-rollen-folder"}
        ).json()["id"]

        assert client.get(f"/api/users/{user_id}/roles", headers=admin).json() == []

        assert client.patch(
            f"/api/users/{user_id}", headers=admin, json={"is_admin": True}
        ).status_code == 200
        before, before_count = _count_queries(
            lambda: client.get(f"/api/users/{user_id}/roles", headers=admin)
        )
        items = before.json()
        assert len(items) == 1
        assert items[0]["role_name"] == "admin"
        assert items[0]["entry_id"] is None

        # Eintrags-ACLs dürfen die Systemliste weder ändern noch zusätzliche
        # Abfragen pro Zeile auslösen.
        client.post(
            f"/api/entries/{folder_id}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": user_id,
                "perms": ["read", "write"],
            },
        )
        after, after_count = _count_queries(
            lambda: client.get(f"/api/users/{user_id}/roles", headers=admin)
        )
        assert [item["id"] for item in after.json()] == [items[0]["id"]]
        assert after_count == before_count


def test_rollen_endpunkte_nach_cleanup():
    """POST /roles ist entfernt; die Cleanup-Funktion lässt nur admin übrig."""
    from ifs.core import permissions

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        assert client.post(
            "/api/roles", headers=admin, json={"name": "opt-alt", "scope": "system"}
        ).status_code == 410
        with session_scope() as db:
            legacy = roles.create_role(
                db, name="opt-alt", scope=RoleScope.system, permissions=authz.READ
            )
            user = make_user(db, "opt-cleanup-user")
            roles.assign_role(
                db,
                role=legacy,
                principal_type=PrincipalType.user,
                principal_id=user.id,
            )
            permissions.cleanup_roles(db)
            assert [role.name for role in roles.list_roles(db)] == ["admin"]


def test_gruppen_acl_gewaehrt_rechte(db):
    """Gruppen-Grant über ACL (statt Ressourcenrolle)."""
    owner = make_user(db, "opt-owner")
    member = make_user(db, "opt-member")
    group = Group(name="opt-team")
    group.members.append(member)
    db.add(group)
    db.flush()

    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "opt-share", owner.id)
    db.add(
        ACL(
            entry_id=folder.id,
            principal_type=PrincipalType.group,
            principal_id=group.id,
            perms=authz.READ,
            inherited=True,
        )
    )
    db.flush()

    assert authz.can(db, member, "read", folder)
    assert not authz.can(db, member, "write", folder)
    # ACL-Grants erscheinen nicht in der (Admin-)Systemliste.
    assert roles.list_system_assignments(db, member.id) == []


def test_letzter_aktiver_admin_bleibt_geschuetzt():
    """Der letzte aktive Administrator kann sich nicht selbst deaktivieren."""
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        admin_id = client.get("/api/users?q=admin", headers=admin).json()["items"][0]["id"]

        response = client.post(f"/api/users/{admin_id}/deactivate", headers=admin)
        assert response.status_code == 409, response.text
        assert client.get(f"/api/users/{admin_id}", headers=admin).json()["is_active"] is True
