# Tutorial 8 – IFS aus Python anbinden

**Ziel:** Eine kleine, wiederverwendbare Python-Klasse für Login, Listing, Upload,
Download und resumable Upload.

**Voraussetzungen:** Python 3.11+, `pip install httpx`.

---

## 1. Minimaler Client

```python
# ifs_client.py
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx


class IFSClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=None)
        self._login(username, password)

    def _login(self, username: str, password: str) -> None:
        response = self._client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        self._client.headers["Authorization"] = f"Bearer {token}"

    # --- Namespace ------------------------------------------------------
    def list(self, parent_id: str | None = None) -> list[dict]:
        params = {"parent_id": parent_id} if parent_id else {}
        return self._client.get("/api/entries", params=params).json()

    def mkdir(self, name: str, parent_id: str | None = None) -> dict:
        body = {"name": name}
        if parent_id:
            body["parent_id"] = parent_id
        response = self._client.post("/api/folders", json=body)
        response.raise_for_status()
        return response.json()

    def find(self, parent_id: str | None, name: str) -> dict | None:
        return next((e for e in self.list(parent_id) if e["name"] == name), None)

    # --- Transfer -------------------------------------------------------
    def upload(self, local_path: str | Path, parent_id: str, name: str | None = None) -> dict:
        path = Path(local_path)
        response = self._client.put(
            "/api/uploads/simple",
            params={"parent_id": parent_id, "name": name or path.name},
            content=path.read_bytes(),
        )
        response.raise_for_status()
        return response.json()

    def download(self, entry_id: str, target_path: str | Path) -> Path:
        with self._client.stream("GET", f"/api/entries/{entry_id}/content") as response:
            response.raise_for_status()
            target = Path(target_path)
            with target.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
        return target

    def resumable_upload(
        self, local_path: str | Path, parent_id: str, chunk_size: int = 1024 * 1024
    ) -> dict:
        path = Path(local_path)
        size = path.stat().st_size

        session = self._client.post(
            "/api/uploads",
            json={
                "parent_id": parent_id,
                "name": path.name,
                "size": size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            },
        ).json()
        upload_id, offset = session["id"], session["offset"]

        with path.open("rb") as handle:
            while offset < size:
                handle.seek(offset)
                chunk = handle.read(chunk_size)
                response = self._client.patch(
                    f"/api/uploads/{upload_id}",
                    headers={
                        "Upload-Offset": str(offset),
                        "Content-Type": "application/offset+octet-stream",
                    },
                    content=chunk,
                )
                if response.status_code == 409:
                    # Offset neu erfragen und fortsetzen
                    offset = int(
                        self._client.head(f"/api/uploads/{upload_id}").headers["Upload-Offset"]
                    )
                    continue
                response.raise_for_status()
                offset = int(response.headers["Upload-Offset"])

        result = self._client.post(f"/api/uploads/{upload_id}/complete")
        result.raise_for_status()
        return result.json()


if __name__ == "__main__":
    ifs = IFSClient("http://localhost:8000", "admin", os.environ["IFS_ADMIN_PASSWORD"])

    project = ifs.mkdir("python-demo")
    print("Ordner:", project["path"])

    info = ifs.upload(__file__, project["id"])   # lädt diese Datei hoch
    print("Hochgeladen:", info)

    entries = ifs.list(project["id"])
    print("Inhalt:", [e["name"] for e in entries])

    target = ifs.download(entries[0]["id"], "zurueck.py")
    print("Heruntergeladen nach:", target)
```

Ausführen:

```bash
python ifs_client.py
```

---

## 2. Fehlerbehandlung

Die API nutzt zwei Fehlerformate:

- Kernfehler: `{"code": "NotFound", "message": "..."}`
- Framework/Validierung: `{"detail": "..."}`

```python
def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text}
    message = payload.get("message") or payload.get("detail") or response.text
    raise RuntimeError(f"IFS-Fehler {response.status_code}: {message}")
```

Behandle insbesondere:

| Status | Bedeutung | Empfehlung |
|---|---|---|
| `401` | Token abgelaufen | neu einloggen |
| `403` | kein Recht | Rechte prüfen |
| `409` | Offset/Namenskonflikt | Offset neu lesen / anderen Namen wählen |
| `413` | zu groß | Limit prüfen |
| `416` | Range ungültig | Größe per `HEAD` ermitteln |

---

## 3. Robuster Download mit Resume

```python
def download_resumable(client: IFSClient, entry_id: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    offset = target.stat().st_size if target.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client._client.stream("GET", f"/api/entries/{entry_id}/content", headers=headers) as r:
        r.raise_for_status()
        mode = "ab" if offset else "wb"
        with target.open(mode) as handle:
            for chunk in r.iter_bytes(1024 * 1024):
                handle.write(chunk)
```

---

## 4. Parallele Uploads

Der Server ist zustandslos; du kannst mehrere Uploads parallel senden. Nutze je Upload
eine eigene Session und begrenze die Nebenläufigkeit, z. B. mit
`concurrent.futures.ThreadPoolExecutor(max_workers=4)`.

---

## 5. Weiterführend

- Vollständige Endpunktliste: [API-Referenz](../docs/api-referenz.md)
- Resumable Details: [Tutorial 4](04-resumable-uploads.md)
- Freigaben programmatisch: `POST /api/shares`

Weiter mit [Tutorial 9 – Produktiv-Deployment](09-produktiv-deployment.md).
