"""RCE-Regressionssuite.

Zwei Ebenen:
1. AST-Guard: verbietet Code-Ausführungs-/Deserialisierungs-/Shell-/Template-
   Sinks in ``src/ifs`` (mit enger Allowlist für ``re.compile`` und
   ``getattr`` mit literalem Attribut).
2. Marker-Payloads: Shell-, Python-Eval-, Template- und ZIP-Payloads bleiben
   Daten; es entsteht keine Markerdatei.
"""

from __future__ import annotations

import ast
import io
import uuid
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_store_blob, fixed_get_object, login

SRC = Path(__file__).resolve().parents[1] / "src" / "ifs"
# Der Inhalt ist absichtlich ein Shell-Skript: Er darf nur als Datum dienen.
PAYLOAD = b"#!/bin/sh\ntouch /tmp/ifs-rce-should-not-exist\n"
_get_object = fixed_get_object(PAYLOAD)

FORBIDDEN_IMPORTS = {
    "subprocess",
    "pickle",
    "marshal",
    "yaml",
    "ctypes",
    "pty",
    "importlib",
    "tarfile",
    "jinja2",
    "mako",
    "django",
    "flask",
}
FORBIDDEN_NAMES = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "render_template_string",
    "Template",
}
FORBIDDEN_ATTRS = {
    "system",
    "popen",
    "spawn",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "execv",
    "execve",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execvp",
    "execvpe",
    "extract",
    "extractall",
    "unpack_archive",
}


def _call_name(node: ast.Call):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _scan_rce_sinks() -> list[str]:
    findings: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in FORBIDDEN_IMPORTS:
                        findings.append(f"{rel}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    findings.append(f"{rel}:{node.lineno}: from {node.module}")
            elif isinstance(node, ast.Call):
                name = _call_name(node)
                if isinstance(node.func, ast.Name) and name in FORBIDDEN_NAMES:
                    findings.append(f"{rel}:{node.lineno}: call {name}()")
                if isinstance(node.func, ast.Attribute) and name in FORBIDDEN_ATTRS:
                    findings.append(f"{rel}:{node.lineno}: call .{name}()")
                if isinstance(node.func, ast.Name) and name in {"getattr", "setattr", "delattr"}:
                    if len(node.args) < 2:
                        continue
                    attr = node.args[1]
                    base = node.args[0]
                    literal = isinstance(attr, ast.Constant) and isinstance(attr.value, str)
                    # Einzige bewusste Ausnahme: Log-Level aus der Admin-Env.
                    logging_level = isinstance(base, ast.Name) and base.id == "logging"
                    if not literal and not logging_level:
                        findings.append(
                            f"{rel}:{node.lineno}: dynamic {name}(..., non-literal)"
                        )
    return findings


def test_no_rce_sinks_in_src():
    findings = _scan_rce_sinks()
    assert findings == [], "RCE-Sinks gefunden:\n" + "\n".join(findings)


def _admin(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}


def _folder(client, headers, name):
    return client.post("/api/folders", headers=headers, json={"name": name})


def _file(client, headers, folder_id, name, data=PAYLOAD):
    return client.put(
        "/api/uploads/simple",
        params={"parent_id": str(folder_id), "name": name},
        headers=headers,
        content=data,
    )


def _marker_payloads(marker: str) -> list[str]:
    """Payloads, die bei Ausführung eine Markerdatei erzeugen würden."""
    return [
        f"$(touch {marker})",
        f"`touch {marker}`",
        f"; touch {marker}; #",
        f'__import__("builtins").open("{marker}","w").write("x")',
        "{{7*7}}",
        "{{ cycler.__init__.__globals__.os.popen('type nul > " + marker + "').read() }}",
    ]


def test_marker_payloads_stay_data(monkeypatch, tmp_path):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _get_object)

    marker_name = f"rce-marker-{uuid.uuid4().hex}"
    marker = Path(marker_name)
    if marker.exists():
        marker.unlink()

    with TestClient(main.app) as client:
        admin = _admin(client)
        root_folder = _folder(client, admin, "rce-root")
        assert root_folder.status_code == 201, root_folder.text
        folder_id = root_folder.json()["id"]

        payloads = _marker_payloads(marker_name)

        # Namen bleiben literal; nichts wird ausgeführt.
        for payload in payloads:
            response = _folder(client, admin, payload)
            assert response.status_code == 201, (payload, response.text)
            # Gleichheit beweist auch für {{7*7}}, dass kein Template gerendert wurde.
            assert response.json()["name"] == payload

        # Datei mit Payload-Namen + Shell-Inhalt; Download liefert exakt die Daten.
        file_name = payloads[3]
        response = _file(client, admin, folder_id, file_name)
        assert response.status_code in (200, 201), response.text
        entry = response.json()
        assert entry["name"] == file_name
        download = client.get(f"/api/entries/{entry['id']}/content", headers=admin)
        assert download.status_code == 200
        assert download.content == PAYLOAD

        # ZIP-Schreibpfad: Payload-Name landet literal im Archiv; keine Extraktion.
        assert (
            client.post(
                f"/api/published/{folder_id}", headers=admin
            ).status_code == 201
        )
        archive = client.get(f"/api/published/{folder_id}/content")
        assert archive.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
            names = zf.namelist()
        assert any(file_name in name for name in names), names
        assert not marker.exists()

        # Such-/Template-/Login-Payloads.
        for payload in payloads:
            assert client.get(
                "/api/users", params={"q": payload}, headers=admin
            ).status_code == 200
            assert client.post(
                f"/api/published/{entry['id']}",
                headers=admin,
                json={"public_suffix": payload},
            ).status_code == 400
        assert client.post(
            "/api/auth/login",
            json={"username": payloads[3], "password": "irrelevant"},
        ).status_code == 401

        # Benutzername als Payload (Second Order); kein Code, kein Marker.
        username = "rce' + " + payloads[3]
        username = username[:200]
        created = client.post(
            "/api/users", headers=admin,
            json={"username": username, "password": "pw-12345678"},
        )
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]
        token = login(client, username, "pw-12345678")
        assert token
        assert client.get(
            "/api/root", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200
        assert client.delete(f"/api/users/{user_id}", headers=admin).status_code == 204

        assert not marker.exists()

    marker.unlink(missing_ok=True)


def test_path_traversal_names_are_rejected(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    with TestClient(main.app) as client:
        admin = _admin(client)
        for name in ("../escape", "..\\escape", "..", "a/b"):
            assert _folder(client, admin, name).status_code == 400, name
        # Kein Traversal ohne Slash: '..foo' ist ein normaler Name.
        ok = _folder(client, admin, "..foo")
        assert ok.status_code == 201
        assert ok.json()["name"] == "..foo"
