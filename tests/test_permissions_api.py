"""API-Tests für Systemrechte (audit/monitor) und Admin-Abgrenzung."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main

from .helpers import login


def _make_user(client: TestClient, admin: dict, username: str) -> tuple[str, dict]:
    response = client.post(
        "/api/users", headers=admin, json={"username": username, "password": "geheim123"}
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {login(client, username, 'geheim123')}"}
    return response.json()["id"], headers


def _set_permissions(
    client: TestClient, admin: dict, principal: str, perms: list[str], *, group: bool = False
) -> int:
    path = "/api/groups/" if group else "/api/users/"
    return client.put(
        f"{path}{principal}/permissions", headers=admin, json={"permissions": perms}
    ).status_code


def test_audit_und_monitor_getrennt_von_admin():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        audit_id, audit_user = _make_user(client, admin, "perm-audit")
        monitor_id, monitor_user = _make_user(client, admin, "perm-monitor")
        normal_id, _normal_user = _make_user(client, admin, "perm-normal")

        # Ohne Grant: 403 (Adminrechte gelten hier nicht).
        assert client.get("/api/audit", headers=audit_user).status_code == 403
        assert client.get("/api/system/stats", headers=audit_user).status_code == 403
        assert client.get("/api/users", headers=audit_user).status_code == 403

        assert _set_permissions(client, admin, audit_id, ["audit"]) == 200
        assert client.get("/api/audit", headers=audit_user).status_code == 200
        assert client.get("/api/system/stats", headers=audit_user).status_code == 403

        assert _set_permissions(client, admin, monitor_id, ["monitor"]) == 200
        assert client.get("/api/system/stats", headers=monitor_user).status_code == 200
        assert client.get("/api/audit", headers=monitor_user).status_code == 403

        # Systemrechte erlauben keine Mutation von Benutzern/Rollen.
        assert client.post(
            "/api/users",
            headers=audit_user,
            json={"username": "verboten", "password": "geheim123"},
        ).status_code == 403
        assert client.put(
            f"/api/users/{normal_id}/permissions",
            headers=monitor_user,
            json={"permissions": ["audit"]},
        ).status_code == 403

        # /api/me liefert die Systemrechte; Admin bleibt effektiv Admin.
        assert client.get("/api/me", headers=audit_user).json()["permissions"] == ["audit"]
        assert client.get("/api/me", headers=monitor_user).json()["permissions"] == ["monitor"]
        me = client.get("/api/me", headers=admin).json()
        assert me["is_admin"] is True and me["permissions"] == []
        assert client.get("/api/audit", headers=admin).status_code == 200
        assert client.get("/api/system/stats", headers=admin).status_code == 200

        # Änderung wird auditiert.
        rows = client.get(
            "/api/audit?action=system_permission.set", headers=admin
        ).json()
        assert rows


def test_systemrechte_nur_audit_monitor_und_gruppen_werden_unterstuetzt():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        user_id, user = _make_user(client, admin, "perm-group-user")

        # ADMIN und Eintragsrechte sind als System-Grant illegal.
        assert _set_permissions(client, admin, user_id, ["admin"]) == 400
        assert _set_permissions(client, admin, user_id, ["read"]) == 400

        group = client.post(
            "/api/groups", headers=admin, json={"name": "perm-audit-group"}
        )
        assert group.status_code == 201, group.text
        group_id = group.json()["id"]
        assert client.post(
            f"/api/users/{user_id}/groups/{group_id}", headers=admin
        ).status_code == 204

        assert _set_permissions(client, admin, group_id, ["audit"], group=True) == 200
        assert client.get("/api/me", headers=user).json()["permissions"] == ["audit"]
        assert client.get("/api/audit", headers=user).status_code == 200

        fetched = client.get(
            f"/api/groups/{group_id}/permissions", headers=admin
        ).json()
        assert fetched["permissions"] == 64
        assert fetched["permission_names"] == ["audit"]

        # Entzug wirkt sofort (Flush invalidiert den Authz-Cache).
        assert _set_permissions(client, admin, group_id, [], group=True) == 200
        assert client.get("/api/audit", headers=user).status_code == 403
