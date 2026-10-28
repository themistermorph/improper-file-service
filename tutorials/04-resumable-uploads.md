# Tutorial 4 – Resumable Uploads

**Ziel:** Große Dateien in Chunks hochladen, unterbrechen, fortsetzen und prüfen.

**Voraussetzungen:** laufendes IFS; `curl` oder Python 3.11+; ein gültiger Token.

---

## 1. Prinzip

Der resumable Upload ist an das **tus**-Protokoll angelehnt:

```
POST /api/uploads                 → Session anlegen, Offset 0
PATCH /api/uploads/{id}           → Chunk anhängen (Header Upload-Offset)
PUT  /api/uploads/{id}/parts/{n}  → Part parallel senden (Part-Session)
HEAD  /api/uploads/{id}           → Offset/Parts abfragen
POST  /api/uploads/{id}/complete  → abschließen (Blob + Version anlegen)
DELETE /api/uploads/{id}          → abbrechen
```

Der Server prüft bei jedem `PATCH`, dass der `Upload-Offset` exakt dem erwarteten Offset
entspricht (sonst `409`). Empfangene Chunks werden im Spool-Verzeichnis
(`IFS_UPLOAD_SPOOL_DIR`) gesammelt und erst bei `complete` nach S3 übertragen.

Für große Dateien gibt es zusätzlich **Part-Sessions**: Die Web-UI überträgt ab
8 MiB standardmäßig 4 Parts à 16 MiB parallel und out-of-order. Jeder Part wird
atomar gespeichert, ein Retry ist idempotent. Details in
[HTTP-Transfers](../docs/http-transfers.md#32-paralleler-part-upload).

---

## 2. Vorbereitung

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Zielordner ermitteln (hier: vorhandener Ordner "projekte")
FOLDER=$(curl -s -H "Authorization: Bearer $TOKEN" "$HOST/api/entries" \
  | python -c "import sys,json;print([e['id'] for e in json.load(sys.stdin) if e['name']=='projekte'][0])")

# Testdatei ~5 MB
head -c 5242880 /dev/urandom > big.bin     # Linux/macOS
# Windows (PowerShell): fsutil file createnew big.bin 5242880
```

---

## 3. Variante A – Shell-Skript mit curl

```bash
set -e
FILE=big.bin
SIZE=$(wc -c < "$FILE")
CHUNK=1048576          # 1 MiB

# Session anlegen
UPLOAD=$(curl -s -X POST "$HOST/api/uploads" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"parent_id\":\"$FOLDER\",\"name\":\"big.bin\",\"size\":$SIZE}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "Upload-Session: $UPLOAD"

OFFSET=0
while [ "$OFFSET" -lt "$SIZE" ]; do
  dd if="$FILE" bs=1M skip=$((OFFSET / CHUNK)) count=1 of=chunk.part 2>/dev/null
  echo "-> sende Offset $OFFSET"
  curl -s -X PATCH "$HOST/api/uploads/$UPLOAD" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Upload-Offset: $OFFSET" \
    -H 'Content-Type: application/offset+octet-stream' \
    --data-binary @chunk.part -o /dev/null -w "   offset jetzt: %header{Upload-Offset}\n"
  OFFSET=$((OFFSET + CHUNK))
done

# Abschließen
curl -s -X POST "$HOST/api/uploads/$UPLOAD/complete" \
  -H "Authorization: Bearer $TOKEN"
```

### Unterbrechung simulieren

Brich das Skript nach einigen Chunks ab und frage den Status ab:

```bash
curl -sI -X HEAD "$HOST/api/uploads/$UPLOAD" \
  -H "Authorization: Bearer $TOKEN" | grep -i upload-offset
```

Setze dann `OFFSET` auf diesen Wert und führe die Schleife fort.

---

## 4. Variante B – Python mit `httpx`

```python
import hashlib
import httpx

HOST = "http://localhost:8000"
TOKEN = "<bearer-token>"
FOLDER_ID = "<ordner-uuid>"
PATH = "big.bin"

data = open(PATH, "rb").read()
headers = {"Authorization": f"Bearer {TOKEN}"}

with httpx.Client(base_url=HOST, headers=headers, timeout=None) as client:
    # Session anlegen
    session = client.post(
        "/api/uploads",
        json={
            "parent_id": FOLDER_ID,
            "name": "big.bin",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    ).json()
    upload_id = session["id"]
    offset = session["offset"]

    # Chunks senden (hier: aus dem Speicher; produktiv dateiweise lesen)
    chunk_size = 1024 * 1024
    while offset < len(data):
        chunk = data[offset : offset + chunk_size]
        response = client.patch(
            f"/api/uploads/{upload_id}",
            headers={
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            },
            content=chunk,
        )
        response.raise_for_status()
        offset = int(response.headers["Upload-Offset"])
        print(f"Fortschritt: {offset}/{len(data)}")

    result = client.post(f"/api/uploads/{upload_id}/complete").json()
    print("Fertig:", result)
```

> **Tipp:** Große Dateien nicht komplett in den Speicher laden – lies die Datei
> stückweise mit `file.read(chunk_size)` und sende jeden Block. Der Server erwartet,
> dass `Upload-Offset` fortlaufend ist.

---

## 5. Variante C – Parallele Parts (für große Dateien)

Die Web-UI nutzt diesen Weg automatisch. Für Skripte:

```python
import concurrent.futures
import httpx

HOST = "http://localhost:8000"
TOKEN = "<bearer-token>"
FOLDER_ID = "<ordner-uuid>"
PATH = "big.bin"
SIZE = __import__("os").path.getsize(PATH)
headers = {"Authorization": f"Bearer {TOKEN}"}

with httpx.Client(base_url=HOST, headers=headers, timeout=None) as client:
    session = client.post("/api/uploads", json={
        "parent_id": FOLDER_ID, "name": "big.bin",
        "size": SIZE, "parallel": True,
    }).json()
    upload_id = session["id"]
    part_size = session["part_size"]
    parts = (SIZE + part_size - 1) // part_size

    def put_part(number: int) -> None:
        start = (number - 1) * part_size
        with open(PATH, "rb") as handle:
            handle.seek(start)
            data = handle.read(part_size)
        response = client.put(
            f"/api/uploads/{upload_id}/parts/{number}",
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(put_part, range(1, parts + 1)))

    # Status: Upload-Parts zeigt alle vorhandenen Parts.
    print(client.head(f"/api/uploads/{upload_id}").headers["Upload-Parts"])
    print(client.post(f"/api/uploads/{upload_id}/complete").json())
```

- `part_size` und `parallelism` kommen aus der Session-Antwort; nicht hart kodieren.
- Parts dürfen in beliebiger Reihenfolge eintreffen und dürfen wiederholt werden.
- Der letzte Part darf kleiner als `part_size` sein; alle anderen müssen exakt passen.
- Bei einem Fehler nach mehreren Versuchen die Session per `DELETE` abbrechen.

---

## 6. Mit Prüfsumme absichern

Gibst du beim Anlegen `sha256` an, vergleicht der Server die Prüfsumme beim Abschluss.
Stimmt sie nicht, antwortet er mit `400` und legt **keinen** Eintrag an.

```python
sha = hashlib.sha256(open("big.bin", "rb").read()).hexdigest()
# ... "sha256": sha im POST-Body
```

---

## 7. Abbrechen und Aufräumen

```bash
curl -s -X DELETE "$HOST/api/uploads/$UPLOAD" -H "Authorization: Bearer $TOKEN"
```

Abgebrochene Sessions sowie Sessions, die 24 Stunden unverändert blieben, entfernt der
Worker automatisch – inklusive der zugehörigen Spool-Dateien.

---

## 8. Häufige Fehler

| Antwort | Ursache | Lösung |
|---|---|---|
| `409` bei `PATCH` | `Upload-Offset` passt nicht | aktuellen Offset per `HEAD` lesen |
| `409` bei Part-`PUT` | Part-Größe weicht ab oder Session ist keine Part-Session | `part_size`/`Upload-Parts` per `HEAD` prüfen |
| `400` bei Part-`PUT` | Body ist nicht exakt `part_size` Bytes (letzter Part ausgenommen) | Dateigröße/letzten Part prüfen |
| `413` | Größenlimit überschritten | `IFS_MAX_UPLOAD_SIZE` prüfen |
| `400` bei `complete` | Größe/SHA-256 stimmt nicht oder Part fehlt | erwartete Werte/`Upload-Parts` prüfen |
| `404` | Session abgelaufen/abgebrochen | neue Session anlegen |

Weiter mit [Tutorial 5 – Benutzer, Gruppen und Rechte](05-benutzer-gruppen-rechte.md).
