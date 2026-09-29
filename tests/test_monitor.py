"""Tests für das Admin-Monitoring (/api/system/stats)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import system_stats


def _login(client, username="admin", password="admin") -> str:
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]


def test_system_stats_requires_admin():
    with TestClient(main.app) as client:
        assert client.get("/api/system/stats").status_code == 401

        admin = {"Authorization": f"Bearer {_login(client)}"}
        client.post("/api/users", headers=admin, json={"username": "mon", "password": "geheim123"})
        user = {"Authorization": f"Bearer {_login(client, 'mon', 'geheim123')}"}
        assert client.get("/api/system/stats", headers=user).status_code == 403

        response = client.get("/api/system/stats", headers=admin)
        assert response.status_code == 200
        data = response.json()
        for key in ("cpu", "memory", "disk", "network", "uptime_seconds", "ifs", "seaweedfs"):
            assert key in data
        assert set(data["ifs"]) == {"users", "entries", "stored_bytes"}
        assert set(data["cpu"]) == {"cores", "percent", "load_1", "load_5", "load_15"}
        assert data["seaweedfs"] is None  # kein Status-Endpunkt konfiguriert


def test_collect_returns_metrics(db):
    from .conftest import make_user

    make_user(db, "statsuser")
    stats = system_stats.collect(db, "/")
    assert stats["ifs"]["users"] >= 1
    assert isinstance(stats["network"], dict)
    # Auf Nicht-Linux (z. B. Windows) sind /proc-Werte None, aber die Struktur steht.
    assert "percent" in stats["cpu"]


def test_seaweedfs_disabled_returns_none():
    assert system_stats._seaweedfs(None) is None
    assert system_stats._seaweedfs("") is None


def test_seaweedfs_parses_volumes(monkeypatch):
    import io
    import json

    payload = {
        "Volumes": {
            "DataCenters": {
                "DefaultDataCenter": {
                    "DefaultRack": {
                        "127.0.0.1:8080": [
                            {"Id": 1, "Size": 1048, "FileCount": 2},
                            {"Id": 2, "Size": 8, "FileCount": 0},
                        ]
                    }
                }
            }
        }
    }

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        system_stats.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(json.dumps(payload).encode()),
    )
    stats = system_stats._seaweedfs("http://s3:9333/vol/status")
    assert stats == {"volumes": 2, "files": 2, "used_bytes": 1056}


def test_seaweedfs_unreachable_returns_none(monkeypatch):
    def _fail(*args, **kwargs):
        raise OSError("kein Netz")

    monkeypatch.setattr(system_stats.urllib.request, "urlopen", _fail)
    assert system_stats._seaweedfs("http://s3:9333/vol/status") is None
