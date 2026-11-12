"""Tests fuer die HttpOnly-Cookie-Session und die Bearer-Policy.

Die Web-UI speichert kein JWT mehr in JavaScript. Stattdessen setzt der Login
ein ``HttpOnly``-Cookie (``SameSite=Strict``); Bearer-Tokens bleiben fuer
API-Clients erhalten, aber nur aus freigegebenen Netzen oder mit Systemrecht
``api``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.security import create_access_token

ADMIN = {"username": "admin", "password": "admin"}


def _login(client: TestClient, issue_token: bool = False):
    suffix = "?issue_token=false" if not issue_token else ""
    return client.post("/api/auth/login" + suffix, json=ADMIN)


def _set_cookie(response) -> str:
    values = response.headers.get_list("set-cookie")
    assert values, "kein Set-Cookie-Header"
    return next(value for value in values if value.startswith("ifs_session="))


def _cookie_attributes(response) -> list[str]:
    return [part.strip().lower() for part in _set_cookie(response).split(";")[1:]]


def test_login_setzt_httponly_strict_cookie():
    with TestClient(main.app) as client:
        response = _login(client)
        assert response.status_code == 200
        attributes = _cookie_attributes(response)
        assert "httponly" in attributes
        assert "samesite=strict" in attributes
        assert "path=/" in attributes
        assert "max-age=3600" in attributes
        # HTTP (TestClient) darf kein Secure-Flag erzwingen, sonst waere die
        # Entwicklungsumgebung unbrauchbar; ueber HTTPS schon.
        assert "secure" not in attributes


def test_login_ueber_https_setzt_secure():
    with TestClient(main.app, base_url="https://testserver") as client:
        response = _login(client)
        assert response.status_code == 200
        assert "secure" in _cookie_attributes(response)


def test_issue_token_false_liefert_kein_jwt_an_javascript():
    with TestClient(main.app) as client:
        response = _login(client, issue_token=False)
        body = response.json()
        assert body["access_token"] is None
        assert body["token_type"] == "cookie"
        # Das Cookie authentifiziert trotzdem.
        assert client.get("/api/me").status_code == 200


def test_cookie_allein_authentifiziert():
    with TestClient(main.app) as client:
        _login(client, issue_token=False)
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"


def test_session_probe_antwortet_ohne_login_mit_200():
    with TestClient(main.app) as client:
        assert client.get("/api/auth/session").json() == {"authenticated": False}
        _login(client, issue_token=False)
        assert client.get("/api/auth/session").json() == {"authenticated": True}


def test_logout_loescht_cookie_und_sitzung():
    with TestClient(main.app) as client:
        _login(client, issue_token=False)
        assert client.get("/api/me").status_code == 200
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/me").status_code == 401


def test_bearer_bleibt_fuer_api_clients_verfuegbar():
    with TestClient(main.app) as client:
        response = _login(client, issue_token=True)
        token = response.json()["access_token"]
        assert token
        assert response.json()["token_type"] == "bearer"
        me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200


def test_bearer_aus_fremdem_netz_ohne_api_recht_wird_abgelehnt():
    with TestClient(main.app) as local:
        admin_token = _login(local, issue_token=True).json()["access_token"]
        admin = {"Authorization": f"Bearer {admin_token}"}
        created = local.post(
            "/api/users",
            headers=admin,
            json={"username": "bearer-user", "password": "geheim123"},
        )
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]

        user_token = local.post(
            "/api/auth/login",
            json={"username": "bearer-user", "password": "geheim123"},
        ).json()["access_token"]
        assert user_token

    remote = TestClient(main.app, client=("10.13.13.99", 41000))
    headers = {"Authorization": f"Bearer {user_token}"}
    assert remote.get("/api/me", headers=headers).status_code == 401

    # Ohne freigegebenes Netz hilft nur das explizite Systemrecht `api`.
    with TestClient(main.app) as local:
        admin_token = _login(local, issue_token=True).json()["access_token"]
        granted = local.put(
            f"/api/users/{user_id}/permissions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"permissions": ["api"]},
        )
        assert granted.status_code == 200, granted.text
        assert "api" in granted.json()["permission_names"]

    assert remote.get("/api/me", headers=headers).status_code == 200


def test_login_aus_fremdem_netz_ohne_recht_gibt_keinen_bearer_token():
    with TestClient(main.app) as local:
        admin_token = _login(local, issue_token=True).json()["access_token"]
        created = local.post(
            "/api/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"username": "remote-user", "password": "geheim123"},
        )
        assert created.status_code == 201, created.text

    remote = TestClient(main.app, client=("10.13.13.99", 41001))
    response = remote.post(
        "/api/auth/login",
        json={"username": "remote-user", "password": "geheim123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] is None
    assert response.json()["token_type"] == "cookie"

    # Der Admin hat das Systemrecht `api` und bekommt daher einen Token,
    # auch wenn die IP nicht freigegeben ist.
    response = remote.post("/api/auth/login", json=ADMIN)
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_fremder_origin_wird_bei_cookie_posts_abgelehnt():
    with TestClient(main.app) as client:
        _login(client, issue_token=False)
        blocked = client.post(
            "/api/folders",
            json={"name": "csrf-blocked"},
            headers={"Origin": "https://evil.example"},
        )
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "Forbidden"

        fetched = client.post(
            "/api/folders",
            json={"name": "csrf-allowed"},
            headers={"Origin": "http://testserver"},
        )
        assert fetched.status_code == 201, fetched.text


def test_cross_site_fetch_metadata_wird_abgelehnt():
    with TestClient(main.app) as client:
        _login(client, issue_token=False)
        blocked = client.post(
            "/api/folders",
            json={"name": "csrf-site"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert blocked.status_code == 403


def test_passwortaenderung_invalidiert_cookie():
    with TestClient(main.app) as client:
        _login(client, issue_token=False)
        assert client.get("/api/me").status_code == 200
        changed = client.post(
            "/api/me/password",
            json={"current_password": "admin", "new_password": "neuesPasswort1"},
        )
        assert changed.status_code == 204, changed.text
        # Das alte Cookie traegt die alte token_version und ist jetzt ungueltig.
        assert client.get("/api/me").status_code == 401


def test_manipuliertes_cookie_wird_abgelehnt():
    with TestClient(main.app) as client:
        forged = create_access_token(
            "00000000-0000-0000-0000-000000000000",
            {"username": "admin", "admin": True, "tv": 0},
        )
        client.cookies.set("ifs_session", forged)
        assert client.get("/api/me").status_code == 401
