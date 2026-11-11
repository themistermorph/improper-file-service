"""Log-Panel: Backend-Tail und statische UI-Bindungen."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import logs

from .helpers import login

INDEX = Path(__file__).resolve().parents[1] / "src/ifs/web/index.html"


def _function_block(source: str, name: str) -> str:
    import re

    match = re.search(rf"\n    (?:async )?function {re.escape(name)}\s*\(", source)
    assert match, f"function {name} nicht gefunden"
    rest = source[match.end():]
    nxt = re.search(r"\n    (?:async )?function \w+\s*\(", rest)
    end = match.end() + nxt.start() if nxt else len(source)
    return source[match.start():end]


def test_logs_tail_und_liste(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    (tmp_path / "api.log").write_text(
        "zeile1\nzeile2\nzeile3\n", encoding="utf-8"
    )
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        response = client.get("/api/logs/api?lines=2", headers=admin)
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["lines"] == ["zeile2", "zeile3"]
        assert body["truncated"] is False

        listing = client.get("/api/logs", headers=admin).json()
        by_service = {item["service"]: item for item in listing}
        assert set(by_service) == {"api", "ftp", "worker"}
        assert by_service["api"]["available"] is True
        assert by_service["ftp"]["available"] is False


def test_logs_fehlen_und_ungueltiger_dienst(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        missing = client.get("/api/logs/worker", headers=admin)
        assert missing.status_code == 200
        assert missing.json()["available"] is False
        assert client.get("/api/logs/unbekannt", headers=admin).status_code == 404
        assert client.get("/api/logs/api?lines=0", headers=admin).status_code == 422
        assert client.get("/api/logs/api?lines=5000", headers=admin).status_code == 422


def test_logs_nur_admin():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        client.post(
            "/api/users",
            headers=admin,
            json={"username": "log-normal", "password": "geheim123"},
        )
        normal = {"Authorization": f"Bearer {login(client, 'log-normal', 'geheim123')}"}
        assert client.get("/api/logs", headers=normal).status_code == 403
        assert client.get("/api/logs/api", headers=normal).status_code == 403


def test_logs_panel_ist_im_adminmenue_und_auto_refresh():
    page = INDEX.read_text(encoding="utf-8")
    assert 'id="menu-logs"' in page
    assert 'id="logs-view"' in page
    assert 'id="log-output"' in page
    load = _function_block(page, "loadLogs")
    assert '"/api/logs/" + service' in load
    sync = _function_block(page, "syncLogsTimer")
    assert 'state.view !== "logs"' in sync
    assert "setInterval(loadLogs" in sync
