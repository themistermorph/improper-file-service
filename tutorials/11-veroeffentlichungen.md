# Tutorial 11 – Veröffentlichungen

**Ziel:** Eine Datei oder einen Ordner in der **öffentlichen Galerie** unter `/published`
bereitstellen, ohne Anmeldung herunterladen und die Veröffentlichung wieder zurückziehen.

**Voraussetzungen:** laufendes IFS; `curl`; ein angemeldeter Benutzer mit `read`- und
`share`-Recht am Eintrag (der Besitzer hat beide implizit).

---

## Überblick

| Schritt | Aufruf | Benötigt |
|---|---|---|
| Veröffentlichen | `POST /api/published/{entry_id}` | `read` + `share` |
| Galerie (ohne Login) | `GET /api/published` (`?q=` zum Filtern) | – |
| Download (ohne Login) | `GET /api/published/{entry_id}/content` | – |
| Eigene auflisten (Admin: alle) | `GET /api/published/mine` | Bearer-Token |
| Zurückziehen | `DELETE /api/published/{entry_id}` | Ersteller, `share`-Berechtigte oder Systemadmin |

Eine Veröffentlichung ist **schlanker als ein Freigabelink** (Tutorial 6): es gibt
**kein Passwort, kein Ablaufdatum und kein Downloadlimit**. Sie wird in der Tabelle
`published_entries` geführt (SQL-Migration
`migrations/sql/0008_published_entries.sql`).

---

## 1. Anmelden und Eintrag vorbereiten

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

# Einen Ordner und eine Datei darin anlegen
FOLDER=$(curl -s -X POST $HOST/api/folders -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"galerie"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

echo "öffentlicher Inhalt" > freigabe.txt
ENTRY=$(curl -s -X PUT "$HOST/api/uploads/simple?parent_id=$FOLDER&name=freigabe.txt" \
  -H "$AUTH" --data-binary @freigabe.txt \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "folder=$FOLDER  entry=$ENTRY"
```

---

## 2. Eintrag veröffentlichen

```bash
curl -s -X POST "$HOST/api/published/$ENTRY" -H "$AUTH" | python -m json.tool
```

Antwort (`201 Created`):

```json
{
  "entry_id": "…",
  "name": "freigabe.txt",
  "type": "file",
  "size": 20,
  "mime": "text/plain",
  "path": "/galerie/freigabe.txt",
  "published_by": "…",
  "published_by_username": "admin",
  "published_at": "2026-10-03T12:00:00Z"
}
```

Das Veröffentlichen erfordert **`read` und `share`** am Eintrag. Wer den Inhalt nicht
lesen darf, darf ihn auch nicht öffentlich zugänglich machen. Ordner werden genauso
veröffentlicht (dann ist `type` `folder`); beim Download liefert IFS den kompletten
Teilbaum als **ZIP**.

### Regeln

- Die eigene **Wurzel** (`parent_id IS NULL`) kann **nicht** veröffentlicht werden →
  `400 Bad Request`. Das verhindert, dass versehentlich das gesamte Heim öffentlich wird.
  Veröffentliche stattdessen einen Ordner oder eine Datei darunter.
- Erneutes Veröffentlichen ist **idempotent**. Es wird kein zweiter Eintrag angelegt und
  die Antwort meldet weiterhin den **ursprünglichen** `published_by_username` – auch
  wenn ein Systemadmin den Aufruf wiederholt.

---

## 3. Galerie abrufen (ohne Anmeldung)

Die öffentliche Galerie ist **ohne Login** erreichbar:

```bash
# Alle Veröffentlichungen
curl -s "$HOST/api/published" | python -m json.tool

# Nach Dateiname filtern (LIKE-Suche, %/_/\ werden literal behandelt)
curl -s "$HOST/api/published?q=freigabe" | python -m json.tool
```

Die Antwort ist bewusst schlank und enthält **keine internen Pfade**:

```json
[
  {
    "entry_id": "…",
    "name": "freigabe.txt",
    "type": "file",
    "size": 20,
    "mime": "text/plain",
    "published_by_username": "admin",
    "published_at": "2026-10-03T12:00:00Z"
  }
]
```

Optional lassen sich die Ergebnisse mit `limit` und `offset` paginieren
(`GET /api/published?q=&limit=&offset=`; Standard `limit=200`, Maximum `500`).

Dieselbe Liste zeigt die **Galerie-Seite** unter <http://localhost:8000/published>
(`GET /published`). Sie lädt ihre Daten ebenfalls aus `GET /api/published`.

---

## 4. Öffentlich herunterladen (ohne Anmeldung)

Datei (mit Range-Support, z. B. für Resume):

```bash
curl -s "$HOST/api/published/$ENTRY/content" -o freigabe.txt
cat freigabe.txt

# Teilbereich laden → 206 Partial Content
curl -s -H "Range: bytes=0-4" -D - \
  "$HOST/api/published/$ENTRY/content" -o teil.txt
```

Ordner (als ZIP):

```bash
curl -s "$HOST/api/published/$FOLDER/content" -o galerie.zip
unzip -l galerie.zip
```

Für den Download ist **kein** Bearer-Token nötig. Es gibt **kein Ablaufdatum und kein
Downloadlimit**: Eine Veröffentlichung bleibt gültig, bis sie zurückgezogen oder der
Eintrag gelöscht wird – danach liefert die URL `404 Not Found`.

---

## 5. Eigene Veröffentlichungen auflisten

```bash
curl -s "$HOST/api/published/mine" -H "$AUTH" | python -m json.tool
```

Angemeldete Benutzer sehen hier ihre **eigenen** Veröffentlichungen inklusive `path`.
**Systemadmins** sehen zusätzlich die Veröffentlichungen **aller** Benutzer – bei
**fremden** Einträgen bleibt `path` jedoch aus Sicherheitsgründen `null`
(Per-User-Isolation; siehe [Sicherheit](../docs/sicherheit.md)).

---

## 6. Veröffentlichung zurückziehen

```bash
curl -s -X DELETE "$HOST/api/published/$ENTRY" -H "$AUTH" -o /dev/null -w "%{http_code}\n"
# 204

# Danach: Galerie und Download liefern 404
curl -s "$HOST/api/published/$ENTRY/content" -o /dev/null -w "%{http_code}\n"
```

Zurückziehen darf der **Ersteller**, wer `share` am Eintrag hat, oder ein **Systemadmin**
(Missbrauchsschutz). Automatisch widerrufen wird eine Veröffentlichung außerdem, wenn:

- der Eintrag gelöscht wird (`DELETE /api/entries/{id}`, Papierkorb), oder
- das veröffentlichende Konto **deaktiviert** oder **gelöscht** wird.

---

## Betrieb und DoS-Schutz

Anonyme Downloads von Veröffentlichungen sind **rate-limitiert** – pro Client-IP und
Eintrag. Standard sind **120 Anfragen** je **300 Sekunden**:

| Einstellung | Standard | Wirkung |
|---|---|---|
| `IFS_PUBLISHED_DOWNLOAD_MAX_REQUESTS` | `120` | Maximale Downloads je Eintrag+IP und Zeitfenster |
| `IFS_PUBLISHED_DOWNLOAD_WINDOW_SECONDS` | `300` | Länge des Zeitfensters (Sekunden) |

Bei Überschreitung antwortet der Dienst mit **`429 Too Many Requests`** und setzt den
Header `Retry-After` (Sekunden bis zum nächsten Versuch). Das gilt für Datei- und
Ordner-Downloads gleichermaßen.

---

## Querverweise

- Endpunkt-Referenz: [API-Referenz – 7. Veröffentlichungen](../docs/api-referenz.md#7-veröffentlichungen)
- Hintergründe und Restrisiken: [Sicherheitshandbuch](../docs/sicherheit.md)
- Verwandtes Thema: [Tutorial 6 – Freigabelinks](06-freigabelinks.md)

Zurück zum [Dokumentationsindex](../docs/README.md).
