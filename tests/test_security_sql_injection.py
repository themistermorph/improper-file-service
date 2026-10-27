"""SQL-Injection-Regressionssuite.

Nachweis, dass alle Nutzereingaben über SQLAlchemy/psycopg als gebundene
Parameter übergeben werden: keine dynamischen SQL-Fragmente, keine
Identifier aus Eingaben, keine Fehlerlecks. Die Payloads sind bewusst
destruktiv/zeitbasiert/Union-basiert; die DB muss danach unverändert arbeiten.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_store_blob, fixed_get_object, login

PAYLOAD = b"sqli-content"
_get_object = fixed_get_object(PAYLOAD)

SQLI = [
    "' OR '1'='1",
    "' OR 1=1 --",
    "'; DROP TABLE entries; --",
    "'; DROP TABLE users; --",
    "\" OR 1=1 --",
    "1 UNION SELECT username, password_hash FROM users --",
    "' AND (SELECT pg_sleep(2)) IS NULL --",
    "admin'--",
    "' OR 'x'='x",
]

# Diese Payloads dürfen nur als Literal wirken; kein Treffer in den Suchen.
ZERO_HIT = {
    "' OR 1=1 --",
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "'; DROP TABLE entries; --",
}

FORBIDDEN = (
    "syntax error",
    "psycopg",
    "sqlalchemy",
    "sqlite",
    "traceback",
    "programmingerror",
    "operationalerror",
    "unterminated",
    "quoted string",
)


def _assert_clean(response) -> None:
    assert response.status_code < 500, (response.status_code, response.text[:800])
    text = response.text.lower()
    for needle in FORBIDDEN:
        assert needle not in text, (needle, response.text[:800])


def _admin(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}


def _folder(client: TestClient, headers: dict[str, str], name: str):
    return client.post("/api/folders", headers=headers, json={"name": name})


def _file(client: TestClient, headers: dict[str, str], folder_id, name: str, data=PAYLOAD):
    return client.put(
        "/api/uploads/simple",
        params={"parent_id": str(folder_id), "name": name},
        headers=headers,
        content=data,
    )


def test_login_payloads_do_not_bypass():
    with TestClient(main.app) as client:
        # Gültiger Login zuerst (legt Admin an, resettet den Account-Zähler).
        assert login(client, "admin", "admin")
        for username, password in (
            ("admin' OR '1'='1", "wrong-password"),
            ("admin", "' OR '1'='1"),
        ):
            response = client.post(
                "/api/auth/login", json={"username": username, "password": password}
            )
            assert response.status_code == 401, response.text
            _assert_clean(response)


def test_user_search_payloads_are_bound():
    with TestClient(main.app) as client:
        admin = _admin(client)
        baseline = client.get("/api/users?limit=200", headers=admin).json()["total"]
        for payload in SQLI:
            response = client.get("/api/users", params={"q": payload}, headers=admin)
            assert response.status_code == 200, response.text
            _assert_clean(response)
            if payload in ZERO_HIT:
                assert response.json()["total"] == 0, (payload, response.json())
        # Tabelle lebt weiter; Suche/Login funktionieren unverändert.
        assert client.get("/api/users?limit=1", headers=admin).json()["total"] == baseline


def test_entry_names_store_payloads_literally(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _get_object)

    with TestClient(main.app) as client:
        admin = _admin(client)
        sentinel = _folder(client, admin, "sentinel")
        assert sentinel.status_code == 201, sentinel.text

        payload_name = "sqli'; DROP TABLE entries; --"
        payload_folder = _folder(client, admin, payload_name)
        assert payload_folder.status_code == 201, payload_folder.text
        assert payload_folder.json()["name"] == payload_name

        file_name = "' OR 1=1 --.txt"
        response = _file(client, admin, sentinel.json()["id"], file_name)
        assert response.status_code in (200, 201), response.text
        file_entry = response.json()
        assert file_entry["name"] == file_name

        root = client.get("/api/entries", headers=admin)
        assert root.status_code == 200
        _assert_clean(root)
        root_names = {item["name"] for item in root.json()}
        assert payload_name in root_names and "sentinel" in root_names

        children = client.get(
            "/api/entries", params={"parent_id": sentinel.json()["id"]}, headers=admin
        )
        assert children.status_code == 200
        _assert_clean(children)
        assert file_name in {item["name"] for item in children.json()}

        # Einträge bleiben auflösbar; die Tabelle wurde nicht gedroppt.
        assert client.get(
            f"/api/entries/{payload_folder.json()['id']}", headers=admin
        ).status_code == 200
        assert client.get(
            f"/api/entries/{file_entry['id']}/content", headers=admin
        ).content == PAYLOAD


def test_published_suffix_and_search_payloads(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _get_object)

    with TestClient(main.app) as client:
        admin = _admin(client)
        folder = _folder(client, admin, "pubsqli").json()["id"]
        entry = _file(client, admin, folder, "f.txt").json()

        for payload in ("'; DROP TABLE published_entries; --", "' OR 1=1 --", "a/b"):
            response = client.post(
                f"/api/published/{entry['id']}",
                headers=admin,
                json={"public_suffix": payload},
            )
            assert response.status_code == 400, (payload, response.text)
            _assert_clean(response)

        created = client.post(
            f"/api/published/{entry['id']}", headers=admin, json={"public_suffix": "safe"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["public_path"] == "admin/safe"

        for q in ("%", "_", "\\", "' OR 1=1 --", "'; DROP TABLE published_entries; --"):
            response = client.get("/api/published", params={"q": q})
            assert response.status_code == 200, response.text
            _assert_clean(response)
            assert response.json() == [], (q, response.json())

        redirect = client.get(
            "/published/" + quote("admin", safe="") + "/" + quote("' OR 1=1 --", safe="")
        )
        assert redirect.status_code == 404
        _assert_clean(redirect)
        assert client.get("/published/admin/safe", follow_redirects=False).status_code == 307
        assert client.get("/api/published").status_code == 200
        assert client.get(
            f"/api/published/{entry['id']}/content"
        ).content == PAYLOAD


def test_audit_action_payloads_are_bound():
    with TestClient(main.app) as client:
        admin = _admin(client)
        for payload in SQLI:
            response = client.get("/api/audit", params={"action": payload}, headers=admin)
            assert response.status_code == 200, response.text
            _assert_clean(response)
        assert client.get("/api/audit?limit=1", headers=admin).status_code == 200


def test_uuid_tokens_and_pagination_payloads_are_rejected():
    with TestClient(main.app) as client:
        admin = _admin(client)
        for payload in SQLI:
            encoded = quote(payload, safe="")
            entry = client.get(f"/api/entries/{encoded}", headers=admin)
            assert entry.status_code == 422, (payload, entry.status_code, entry.text)
            _assert_clean(entry)

            share = client.get(f"/api/shares/{encoded}/content")
            assert share.status_code in (404, 422), (payload, share.status_code)
            _assert_clean(share)

            preview = client.get(f"/api/preview/{encoded}")
            assert preview.status_code in (401, 404, 422), (payload, preview.status_code)
            _assert_clean(preview)

        for params in (
            {"limit": "' OR 1=1 --"},
            {"offset": "1; DROP TABLE users; --"},
            {"limit": "1 UNION SELECT 1"},
        ):
            response = client.get("/api/users", params=params, headers=admin)
            assert response.status_code == 422, (params, response.status_code, response.text)
            _assert_clean(response)
        assert client.get("/api/users?limit=1", headers=admin).status_code == 200


def test_second_order_username_payload():
    with TestClient(main.app) as client:
        admin = _admin(client)
        username = "sqli' OR '1'='1 --"
        password = "pw-12345678"
        created = client.post(
            "/api/users",
            headers=admin,
            json={"username": username, "password": password},
        )
        assert created.status_code == 201, created.text
        assert created.json()["username"] == username
        user_id = created.json()["id"]

        token = login(client, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/root", headers=headers).status_code == 200

        bad = client.post(
            "/api/auth/login", json={"username": username, "password": "wrong-password"}
        )
        assert bad.status_code == 401
        _assert_clean(bad)

        assert client.delete(f"/api/users/{user_id}", headers=admin).status_code == 204
        assert client.get("/api/users?limit=1", headers=admin).status_code == 200
