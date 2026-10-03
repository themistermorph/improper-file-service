# REST-API-Referenz

Base-URL: `https://<host>/api` (im Dev-Compose: `http://localhost:8000/api`)

Interaktive Dokumentation: `GET /docs` (Swagger UI), `GET /redoc`, Schema unter
`GET /openapi.json`.

---

## 1. Konventionen

### Authentifizierung

Alle Endpunkte außer Login, Health, Version, öffentlicher Galerie (`/api/published`),
Freigabe-Download (`/api/shares/{token}/content`) sowie der tokenbasierten Vorschau- und
Archiv-Endpunkte (`/api/preview/{token}`, `/api/archive/{token}`,
`/api/archive/jobs/{token}`) benötigen einen Bearer-Token:

```
Authorization: Bearer <access_token>
```

Der Token wird über `POST /api/auth/login` bezogen und läuft nach
`IFS_ACCESS_TOKEN_TTL_SECONDS` (Standard 3600 s) ab.

### Datentypen

- IDs sind **UUIDs** (als String).
- Zeitstempel sind **ISO-8601 mit Zeitzone** (UTC), z. B. `2026-09-28T10:15:30Z`.
- Größen sind **Bytes** (Integer).

### Fehlerformat

Fachliche Fehler des Kerns liefern:

```json
{ "code": "NotFound", "message": "Eintrag nicht gefunden" }
```

Validierungs- und Framework-Fehler (FastAPI) liefern:

```json
{ "detail": "..." }
```

### Wichtige Statuscodes

| Code | Bedeutung |
|---|---|
| `200` | OK |
| `201` | Erstellt |
| `204` | OK ohne Inhalt (Löschen, Upload-Chunk) |
| `206` | Teilinhalt (Range-Download) |
| `400` | Ungültige Anfrage |
| `401` | Nicht angemeldet / Token ungültig / Share-Passwort falsch |
| `403` | Keine Berechtigung |
| `404` | Nicht gefunden |
| `409` | Konflikt (Name existiert, Upload-Offset passt nicht) |
| `410` | Freigabe abgelaufen oder Downloadlimit erreicht |
| `412` | Vorbedingung fehlgeschlagen (ETag, reserviert) |
| `413` | Datei zu groß / Quota |
| `416` | Range nicht erfüllbar (mit `Content-Range: bytes */<size>`) |
| `429` | Zu viele Anfragen (Rate-Limit, u. a. Login, Share-Passwort/-Upload, Veröffentlichungs-Download; `Retry-After`) |

### Eintragstyp `EntryOut`

```json
{
  "id": "0192...",
  "parent_id": "0192...",
  "name": "report.pdf",
  "type": "file",
  "path": "/projekte/2026/report.pdf",
  "size": 1048576,
  "mime": "application/pdf",
  "sha256": "9f86d081...",
  "created_at": "2026-09-28T10:15:30Z",
  "updated_at": "2026-09-28T10:15:30Z"
}
```

`type` ist `"folder"` oder `"file"`.

> **Tipp – eigene Wurzel-ID:** `GET /api/root` liefert das eigene Home (Wurzel) inkl.
> `id`. Alternativ listet `GET /api/entries` ohne `parent_id` den Inhalt der eigenen Wurzel.

---

## 2. Authentifizierung & Konto

### `POST /api/auth/login`

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `username` | string | ja | Benutzername |
| `password` | string | ja | Passwort |

Antwort `200`:

```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 3600 }
```

```bash
curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}"
```

Bei Fehlschlag `401` und ein Audit-Eintrag `auth.login.failed`.

### `GET /api/me`

Liefert den angemeldeten Benutzer (`UserOut`).

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/me
```

---

## 3. Namespace / Entries

> **Per-User-Wurzel:** Jeder Benutzer besitzt ein eigenes Home (`parent_id IS NULL`,
> `owner_id = Benutzer`). Alle Endpunkte ohne `parent_id` beziehen sich auf die **eigene**
> Wurzel. Systemadmins haben **keinen Dateizugriff** auf fremde Wurzeln – Zugriff nur über
> Besitz, ACLs und Ressourcenrollen. Freigegebene Einträge listet `GET /api/shared`.

### `GET /api/entries`

Listet die Kinder eines Verzeichnisses.

| Query | Typ | Standard | Beschreibung |
|---|---|---|---|
| `parent_id` | UUID | eigene Wurzel (Home) | Verzeichnis, dessen Inhalt gelistet wird |

Antwort `200`: Array von `EntryOut`, Ordner zuerst, dann alphabetisch.

### `GET /api/entries/{entry_id}`

Liefert einen einzelnen Eintrag (`EntryOut`). `403`/`404` je nach Rechten/Existenz.

### `GET /api/root`

Liefert die **eigene** Wurzel (Home) als `EntryOut`. Praktisch für Clients und die
Web-UI, um die Wurzel-ID zu ermitteln (z. B. für Uploads in die Wurzel).

### `GET /api/shared`

Listet Einträge, die dem Benutzer per ACL oder Ressourcenrolle freigegeben sind
(oberste Einstiegspunkte, ohne eigene Einträge) – Grundlage der UI-Ansicht
„Mit mir geteilt".

### `GET /api/folders`

Listet alle für den Benutzer lesbaren, nicht gelöschten Ordner samt `path` – nützlich für
Auswahl-Dialoge (z. B. „Verschieben“).

### Bulk-Aktionen

| Methode & Pfad | Beschreibung |
|---|---|
| `POST /api/bulk/move` | `{ ids: [...], parent_id }` – verschiebt mehrere Einträge; Antwort `{ ok, failed }` |
| `POST /api/bulk/delete` | `{ ids: [...] }` – verschiebt mehrere in den Papierkorb; Antwort `{ ok, failed }` |

Nicht erlaubte oder fehlschlagende Einträge landen in `failed`, der Rest wird ausgeführt.

### `POST /api/folders`

Legt einen Ordner an. `201`.

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `parent_id` | UUID \| null | nein | Zielordner; fehlt = Wurzel |
| `name` | string (1–255) | ja | Ordnername |

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"docs"}' localhost:8000/api/folders
```

### `PATCH /api/entries/{entry_id}`

Benennt einen Eintrag um. `200`.

| Feld | Typ | Pflicht |
|---|---|---|
| `name` | string (1–255) | ja |

### `POST /api/entries/{entry_id}/move`

Verschiebt einen Eintrag in ein anderes Verzeichnis. `200`. Reine Metadaten-Operation.
Erfordert `write` auf Quelle und Ziel sowie zusätzlich `read` auf der Quelle (verhindert,
dass ein write-only-Eintrag in einen lesbaren Ordner verschoben und dort gelesen wird).

| Feld | Typ | Pflicht |
|---|---|---|
| `parent_id` | UUID | ja |

### `POST /api/entries/{entry_id}/copy`

Kopiert einen Eintrag. Dateien teilen den Blob des Originals (kein Datenkopieren im
Objektspeicher, eigene Version 1); **Ordner werden samt aktivem Teilbaum rekursiv
kopiert** (Papierkorb-Einträge ausgenommen). Die Quota des Kopierenden wird einmalig
für die Summe aller kopierten Dateien geprüft (`413` bei Überschreitung). Erfordert
`write` auf dem Ziel sowie zusätzlich `read` **und** `share` auf der Quelle – eine Kopie
gehört dem Kopierenden und ist damit unabhängig weiterverteilbar. `201`.

| Feld | Typ | Pflicht |
|---|---|---|
| `parent_id` | UUID | ja |
| `name` | string (1–255) | ja |

### `DELETE /api/entries/{entry_id}`

Verschiebt in den Papierkorb (Soft-Delete, inkl. Teilbaum). `204`.

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" localhost:8000/api/entries/$ID
```

---

### Papierkorb

| Methode & Pfad | Beschreibung |
|---|---|
| `GET /api/trash` | Gelöschte Einträge (oberste Ebene) mit `path`, `size`, `files`, `trashed_at`, `days_left` |
| `GET /api/trash/count` | `{ "count": n }` (für Badge/Anzeige) |
| `POST /api/trash/{entry_id}/restore` | Wiederherstellen; Body optional `{ "parent_id": …, "new_name": … }` → `200` mit `EntryOut` |
| `POST /api/trash/bulk/restore` | Mehrere wiederherstellen: `{ "ids": [...] }` → `{ "ok": n, "failed": [...] }` |
| `POST /api/trash/bulk/purge` | Mehrere endgültig löschen: `{ "ids": [...] }` → `{ "ok": n, "failed": [...], "files": m }` |
| `DELETE /api/trash/{entry_id}` | Endgültig löschen (inkl. Teilbaum) → `204` |
| `DELETE /api/trash` | Papierkorb leeren → `200` `{ "entries": n, "files": m }` |

**Regeln:** Ein gelöschter Ordner erscheint **einmal** (nicht mit allen Kindern). Beim
Wiederherstellen wird der Teilbaum reaktiviert; existiert am Ziel bereits ein aktiver
Eintrag gleichen Namens, antwortet die API mit `409` – dann `new_name` mitgeben.
Wiederherstellen erfordert das `delete`-Recht (oder Besitz) am Eintrag **sowie
`read` auf dem Eintrag und `write` auf dem Zielordner** – sonst `403`. Nach
Ablauf von `IFS_TRASH_RETENTION_DAYS` (Standard 30) entfernt der Worker die Einträge
endgültig (Blob-GC entfernt danach unreferenzierte Inhalte).

---

## 4. Download

### `GET /api/entries/{entry_id}/content`

Streamt den Dateiinhalt.

| Query | Typ | Standard | Beschreibung |
|---|---|---|---|
| `download` | bool | `false` | Hängt `Content-Disposition: attachment` an |

**Header der Antwort**

| Header | Beschreibung |
|---|---|
| `Content-Type` | MIME-Typ der Datei |
| `Content-Length` | Größe des (Teil-)Inhalts |
| `ETag` | SHA-256 der Datei (in Anführungszeichen) |
| `Last-Modified` | Änderungszeitpunkt |
| `Accept-Ranges` | immer `bytes` |
| `Content-Range` | nur bei `206`: `bytes <start>-<end>/<size>` |

**Range-Requests** (resumable):

```bash
# Erste 1 MiB
curl -s -H "Authorization: Bearer $TOKEN" \
  -H 'Range: bytes=0-1048575' \
  localhost:8000/api/entries/$ID/content -o part1.bin

# Ab Offset 1 MiB bis Ende
curl -s -H "Authorization: Bearer $TOKEN" \
  -H 'Range: bytes=1048576-' \
  localhost:8000/api/entries/$ID/content -o part2.bin
```

Antwort `206` mit `Content-Range`. Ein ungültiger oder außerhalb liegender Bereich
ergibt `416`.

### `HEAD /api/entries/{entry_id}/content`

Wie `GET`, aber ohne Body (nicht in OpenAPI gelistet). Liefert Größe, Typ, ETag und
`Last-Modified` – nützlich zum Prüfen, ob sich eine Datei geändert hat.

---

### Datei-Vorschau (Token)

Für Browser-eigene Renderer (Bild, Video, Audio, PDF) fordern Clients einen kurzlebigen
Token an, da `<img>`/`<video>`/`<iframe>` keine Authorization-Header senden können.

| Methode & Pfad | Beschreibung |
|---|---|
| `POST /api/entries/{entry_id}/preview-token` | Erzeugt Token; Antwort `{ token, url, expires_in, name, mime, size }`. Erfordert `read`. |
| `GET /api/preview/{token}` | Streamt den Inhalt (Range-fähig, **ohne** Auth-Header); Token läuft nach 5 Minuten ab. |

Text-/Code- und HTML-Dateien lädt die Web-UI direkt (mit `Range`, erste 256 KB) und zeigt
sie als Text bzw. in einem **sandboxed** `<iframe>`. HTML-/SVG-Antworten werden zusätzlich
per `Content-Security-Policy: sandbox` abgesichert.

### ZIP-Download (Ordner)

| Methode & Pfad | Beschreibung |
|---|---|
| `POST /api/entries/{entry_id}/archive-token` | Erzeugt Token; Antwort `{ token, url, expires_in, name }`. Erfordert `read`. |
| `POST /api/archive/bulk-token` | Wie oben, aber für mehrere Einträge: `{ entry_ids: [...] }` → `name` = `auswahl.zip`. |
| `POST /api/archive/jobs` | Startet die ZIP-Erstellung **im Hintergrund** und liefert `{ token, name, url, status_url, expires_in, total_files, total_bytes }`. Erfordert `read`. |
| `GET /api/archive/jobs/{token}` | Fortschritt: `{ status: building\|ready\|failed, files_done, total_files, bytes_done, total_bytes, error }`. Der Token wird geprüft (Signatur, Zweck, `token_version`); ungültig → `401`, kein Job → `404`. |
| `DELETE /api/archive/jobs/{token}` | Bricht den Auftrag ab und löscht ein evtl. fertiges Archiv (`204`). Token-Prüfung wie oben. |
| `GET /api/archive/{token}` | Liefert ein ZIP (`application/zip`) mit dem/den Einträgen – bei Ordnern inkl. Teilbaum. Bei einem Job: `409`, solange er noch läuft. |

Das Archiv wird serverseitig in eine temporäre Datei geschrieben und gestreamt (konstanter
Speicherbedarf), danach automatisch gelöscht.

**Hintergrund-Jobs (Fortschritt):** Die WebUI nutzt `/api/archive/jobs`, pollt den
Fortschritt und startet den Download erst bei `ready`. Der Status-Token ist 1 Stunde
gültig; der prozesslokale Job wird zusätzlich nach 30 Minuten aufgeräumt bzw. nach dem
Download entfernt. Der direkte Weg über `archive-token` bleibt kompatibel und erstellt
das ZIP synchron (Token 5 Minuten gültig).

```bash
JOB=$(curl -s -X POST localhost:8000/api/archive/jobs -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d "{\"entry_ids\":[\"$FOLDER\"]}")
JTOKEN=$(echo "$JOB" | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s localhost:8000/api/archive/jobs/$JTOKEN   # Fortschritt
curl -sO -J localhost:8000/api/archive/$JTOKEN    # fertiges ZIP
```

### Versionen und Rollback

| Methode & Pfad | Beschreibung |
|---|---|
| `GET /api/entries/{entry_id}/versions` | Versionshistorie (neueste zuerst, `is_current`) |
| `GET /api/entries/{entry_id}/versions/{version_id}/content` | Inhalt einer bestimmten Version (Range-fähig) |
| `POST /api/entries/{entry_id}/versions/{version_id}/restore` | Stellt eine Version als **neue** aktuelle Version wieder her |

Jedes Überschreiben erzeugt eine neue Version. Das Rollback ist **append-only**: Die
bisherige aktuelle Version bleibt in der Historie erhalten. Erfordert `read`
(Historie/Download) bzw. `write` (Rollback).

---

## 5. Upload

### 5.1 Einfacher Upload

### `PUT /api/uploads/simple`

Body = Dateiinhalt (Stream). `201`.

| Query | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `parent_id` | UUID | nein | Zielordner; fehlt = Wurzel |
| `name` | string | ja | Dateiname |
| `mime` | string | nein | MIME-Typ |

```bash
curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
  --data-binary @report.pdf \
  "localhost:8000/api/uploads/simple?name=report.pdf"
```

Antwort:

```json
{ "id": "0192...", "name": "report.pdf", "size": 1048576, "sha256": "9f86..." }
```

Einen vorhandenen Namen zu überschreiben erzeugt eine **neue Version**. Das Größenlimit
(`IFS_MAX_UPLOAD_SIZE`) und die Quota (`IFS_DEFAULT_QUOTA_BYTES`) werden **vor** dem
Schreiben geprüft; Überschreitung liefert `413`.

### 5.2 Resumable Upload (tus-ähnlich)

**Schritt 1 – Session eröffnen:** `POST /api/uploads`

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `parent_id` | UUID | ja | Zielordner |
| `name` | string | ja | Dateiname |
| `size` | int | nein | Erwartete Gesamtgröße (wird geprüft) |
| `sha256` | string | nein | Erwartete Prüfsumme (wird geprüft) |
| `mime` | string | nein | MIME-Typ |

Antwort `201`: `{ "id": "<upload-id>", "offset": 0, "status": "pending" }`

**Schritt 2 – Chunk anhängen:** `PATCH /api/uploads/{upload_id}`

| Header | Pflicht | Beschreibung |
|---|---|---|
| `Upload-Offset` | ja | Aktueller Offset; muss dem Server-Offset entsprechen |
| `Content-Type` | empfohlen | `application/offset+octet-stream` |

Body = Chunk-Bytes. Antwort `204` mit Header `Upload-Offset` = neuer Offset.
Bei falschem Offset: `409`.

> **Abbruch und Konsistenz:** Überschreitet der Upload `IFS_MAX_UPLOAD_SIZE`, antwortet
> der Endpunkt mit `413` und die Session ist danach **dauerhaft abgebrochen**
> (`HEAD`/`PATCH` → `404`/`409`). Vor jedem Chunk wird der Spool auf den bestätigten
> Offset gekürzt; beim Abschluss muss die Spool-Größe exakt dem Offset entsprechen
> (`400` sonst). Upload-Sessions sind strikt nutzergebunden – auch Systemadmins können
> fremde Sessions weder abfragen noch abbrechen.

**Schritt 3 – Status abfragen (optional):** `HEAD /api/uploads/{upload_id}`
→ `204` mit `Upload-Offset` und `Upload-Length`.

**Schritt 4 – Abschließen:** `POST /api/uploads/{upload_id}/complete?mime=<typ>`
→ `201` mit `{ id, name, size, sha256 }`. Prüft Größe und ggf. SHA-256, legt den Blob
an und erzeugt eine neue Version.

**Schritt 5 – Abbrechen (optional):** `DELETE /api/uploads/{upload_id}` → `204`.

Vollständiges Beispiel: [Tutorial 4](../tutorials/04-resumable-uploads.md).

---

## 6. Freigaben

### `GET /api/shares?entry_id=`

Listet Freigaben – Nicht-Admins sehen nur ihre eigenen, Administratoren alle. Optional auf
einen Eintrag gefiltert. Zusätzliche Felder: `entry_name`, `entry_path`,
`created_by_username`, `created_at`.

### `POST /api/shares`

Erstellt einen öffentlichen Link. Erfordert `read`- und `share`-Recht am Eintrag (wer den
Inhalt nicht lesen darf, darf ihn auch nicht veröffentlichen). `allow_upload=true`
erfordert zusätzlich `write` am Eintrag; Uploads prüfen das Schreibrecht des Erstellers
erneut. `201`.

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `entry_id` | UUID | ja | Freizugebender Eintrag |
| `expires_at` | datetime | nein | Ablaufzeitpunkt (UTC) |
| `password` | string | nein | Passwortschutz |
| `max_downloads` | int | nein | Maximale Downloadzahl (muss ≥ `0` sein, sonst `422`) |
| `allow_upload` | bool | nein | Upload über den Link erlauben (nur für Ordner; erfordert `write` am Eintrag, siehe `POST /api/shares/{token}/upload`) |
| `overwrite` | bool | nein | Anonymen Uploads das Überschreiben vorhandener Dateien erlauben (nur wirksam mit `allow_upload=true`); Standard `false` |

Antwort `ShareOut`:

```json
{
  "token": "abc123...",
  "entry_id": "0192...",
  "expires_at": "2026-10-01T00:00:00Z",
  "max_downloads": 5,
  "downloads": 0,
  "has_password": true,
  "allow_upload": false,
  "overwrite": false
}
```

### `POST /api/shares/bulk/revoke`

Widerruft mehrere Freigaben in einem Aufruf: `{ "tokens": [...] }` →
`{ "ok": n, "failed": [...] }`. Nicht existierende oder nicht verwaltbare Tokens
landen in `failed`.

### `GET /api/shares/{token}`

Metadaten der Freigabe. `404`, wenn unbekannt; `410`, wenn abgelaufen oder
Downloadlimit erreicht. Bei passwortgeschützten Freigaben ist das Passwort per
`X-Share-Password` erforderlich (`401` sonst, rate-limitiert). Die Antwort trägt
`Cache-Control: no-store`.

### `GET /api/shares/{token}/content`

Öffentlicher Download **ohne** Bearer-Token.

| Header | Pflicht | Beschreibung |
|---|---|---|
| `X-Share-Password` | bei Passwortschutz | Freigabepasswort (Header statt URL – verhindert Leaks in Logs/History) |

> Der frühere Query-Parameter `?password=` wird aus Kompatibilität noch akzeptiert, ist aber
> **veraltet** und sollte nicht mehr verwendet werden.

Zählt `downloads` **atomar** hoch (das Limit `max_downloads` wird dabei durchgesetzt) und
schreibt einen Audit-Eintrag `share.download`. Fehlversuche sind rate-limitiert (`429`).

> **Ordner-Freigaben:** Wird ein Ordner geteilt, liefert dieser Endpunkt den kompletten
> Teilbaum als **ZIP-Archiv** (`Content-Type: application/zip`, Dateiname
> `<Ordnername>.zip`). Passwort, Ablauf und Downloadlimit gelten unverändert; der Abruf
> zählt als ein Download.

```bash
curl -s -H "X-Share-Password: geheim" "localhost:8000/api/shares/$TOKEN/content" -o file.bin
```

### `GET /api/shares/{token}/content/{filename}`

Gleicher Download, jedoch mit dem gewünschten **Dateinamen im Pfad**. Der Name ist für
Tools wie `curl -O` relevant, das (ohne `-J`) den letzten URL-Abschnitt als Dateinamen
verwendet. Empfohlene Link-Form, die auch die WebUI erzeugt:

- Datei-Freigabe: `/api/shares/<token>/content/<Dateiname>`
- Ordner-Freigabe: `/api/shares/<token>/content/<Ordnername>.zip`

```bash
curl -O "localhost:8000/api/shares/$TOKEN/content/bericht.pdf"
curl -O "localhost:8000/api/shares/$TOKEN/content/team.zip"   # Ordner -> ZIP
```

> `Content-Disposition` enthält zusätzlich immer den korrekten Dateinamen; mit
> `curl -OJ` funktioniert es auch über den Basis-Endpunkt `/content`.

### `POST /api/shares/{token}/upload`

Öffentlicher Upload **ohne** Bearer-Token – nur möglich, wenn die Freigabe mit
`allow_upload: true` erstellt wurde **und** auf einen **Ordner** zeigt. Der
Anfrage-Body ist der Dateiinhalt (wie `PUT /api/uploads/simple`). Erlaubt sind
**`POST` und `PUT`**, damit `curl -T` direkt funktioniert.

| Parameter/Header | Pflicht | Beschreibung |
|---|---|---|
| `name` (Query) | ja | Dateiname im geteilten Ordner |
| `mime` (Query) | nein | MIME-Typ (sonst aus dem Namen abgeleitet) |
| `X-Share-Password` | bei Passwortschutz | wie beim Download |

Ohne `overwrite=true` an der **Freigabe** wird bei einem Namenskonflikt automatisch auf
`name (2).ext` ausgewichen, damit **nichts überschrieben** wird; der endgültige Zielname
wird dazu unmittelbar vor dem Schreiben erneut bestimmt – auch **parallele** Uploads
ersetzen dadurch keine vorhandene Datei. Das frühere
Request-Flag `?overwrite=true` hat keine Wirkung mehr; Überschreiben erlaubt nur die
Freigabe-Eigenschaft `overwrite` (siehe `POST /api/shares` und `PATCH /api/shares/{token}`).
Der Endpunkt prüft bei jedem Aufruf erneut, ob der Ersteller der Freigabe noch `write`
am Ordner hat – nach Entzug des Rechts antwortet er mit `403`. Größenlimit,
Besitzer-Quota und Ablauf gelten; der Upload wird als `share.upload` auditiert und dem
Ersteller der Freigabe zugerechnet.

> **Missbrauchsschutz:** Für öffentliche Share-Uploads gilt zusätzlich ein hartes
> Größenlimit (`IFS_SHARE_UPLOAD_MAX_SIZE`, Standard 100 MiB) und ein Rate-Limit pro
> Freigabe+IP (`IFS_SHARE_UPLOAD_MAX_REQUESTS`/`IFS_SHARE_UPLOAD_WINDOW_SECONDS`).
> Beide greifen auch bei `IFS_MAX_UPLOAD_SIZE=0`. Überschreitung → `413` bzw. `429`.

```bash
curl -T bericht.pdf "localhost:8000/api/shares/$TOKEN/upload?name=bericht.pdf"
curl -T bericht.pdf -H "X-Share-Password: geheim" \
  "localhost:8000/api/shares/$TOKEN/upload?name=bericht.pdf"
```

### `DELETE /api/shares/{token}`

Widerruft die Freigabe. `204`. Erfordert Anmeldung und `share`-Recht – **auch wenn der
zugehörige Eintrag bereits im Papierkorb liegt** (Ersteller, `share`-Berechtigte sowie
Systemadmins zum Missbrauchsschutz dürfen widerrufen). Der Widerruf wird als
`share.revoke` auditiert (nur Token-Präfix).

> **Automatischer Widerruf:** Beim Löschen (`DELETE /api/entries/{id}`) werden alle
> Freigabelinks des Eintrags und seines Teilbaums **automatisch widerrufen**.

### `PATCH /api/shares/{token}`

Ändert eine bestehende Freigabe nachträglich (Ersteller oder `share`-Recht erforderlich;
Systemadmins können fremde Freigaben nur **widerrufen**, nicht ändern → `403`). Nur
gesetzte Felder werden geändert. Das Scharfschalten von `allow_upload=true` bzw.
`overwrite=true` erfordert zusätzlich `write` am Eintrag; das Abschalten ist auch ohne
`write` erlaubt (`403` sonst):

| Feld | Wirkung |
|---|---|
| `password` | neues Passwort; `""` entfernt den Passwortschutz |
| `expires_at` | neues Ablaufdatum; `null` = unbegrenzt |
| `max_downloads` | neues Downloadlimit; `null` = unbegrenzt |
| `allow_upload` | Upload über den Link erlauben (erfordert `write` am Eintrag) |
| `overwrite` | anonymen Uploads das Überschreiben erlauben (erfordert `write` und wirkt nur mit `allow_upload=true`) |

Antwort: `ShareDetailOut`. Widerrufen entfernt den Link endgültig (`GET`/Download danach `404`).

---

## 7. Veröffentlichungen

Eine Veröffentlichung stellt eine **Datei oder einen Ordner** ohne Anmeldung in der
öffentlichen Galerie unter `/published` bereit. Ordner werden beim Download als **ZIP**
geliefert (wie bei Freigaben). Die Veröffentlichung ist schlanker als eine Freigabe (kein
Passwort, kein Ablauf, kein konfigurierbares Downloadlimit) und wird in
`published_entries` geführt. Erforderlich sind `read`- und `share`-Recht am Eintrag;
die eigene **Wurzel** (`parent_id IS NULL`) kann nicht veröffentlicht werden (`400`).

| Methode & Pfad | Auth | Beschreibung |
|---|---|---|
| `GET /api/published?q=&limit=&offset=` | – | Öffentliche Galerie: `[{ entry_id, name, type, size, mime, published_by_username, published_at }]`. `q` filtert den Namen; `%`, `_` und `\` werden literal behandelt. |
| `GET /api/published/{entry_id}/content` | – | Öffentlicher Download: Datei (Range-Support) bzw. Ordner (ZIP). Rate-limitiert pro Eintrag+IP (`429` mit `Retry-After`). |
| `GET /api/published/mine` | Bearer | Eigene Veröffentlichungen inkl. `path`; Systemadmins sehen zusätzlich fremde, bei diesen wird `path` ausgelassen. |
| `POST /api/published/{entry_id}` | Bearer | Datei oder Ordner veröffentlichen (`201`; idempotent – erneutes Veröffentlichen meldet weiterhin den ursprünglichen `published_by_username`). Optionaler Body `{ "public_name": "..." }`. |
| `PATCH /api/published/{entry_id}` | Bearer | Öffentlichen Anzeigenamen nachträglich ändern: `{ "public_name": "..." }`; `null`/leer = interner Name. |
| `DELETE /api/published/{entry_id}` | Bearer | Veröffentlichung zurückziehen (`204`). |

Der **öffentliche Anzeigename** (`public_name`, max. 255 Zeichen) ersetzt in der Galerie
und im Download-Dateinamen (`Content-Disposition`, bei Ordnern `<name>.zip`) den internen
Namen. Ist er leer, wird der interne Name verwendet. Verwalten (PATCH/DELETE) darf der
Ersteller, wer `share` am Eintrag hat, oder ein Systemadmin.

Anonyme Downloads sind begrenzt über `IFS_PUBLISHED_DOWNLOAD_MAX_REQUESTS` (Standard 120)
je `IFS_PUBLISHED_DOWNLOAD_WINDOW_SECONDS` (Standard 300 s); bei Überschreitung `429`.

Die Galerie-Seite ist unter `GET /published` erreichbar und lädt ihre Daten aus
`GET /api/published`.

> **Automatischer Widerruf:** Beim Löschen (`DELETE /api/entries/{id}`) sowie beim
> Deaktivieren oder Löschen eines Kontos werden zugehörige Veröffentlichungen
> automatisch zurückgezogen.

Ausführlich: [Tutorial 11](../tutorials/11-veroeffentlichungen.md).

---

## 8. Benutzer und Administration

### 8.1 Selbstbedienung (jeder angemeldete Benutzer)

| Methode & Pfad | Beschreibung |
|---|---|
| `GET /api/me` | Eigenes Profil (`UserOut`) |
| `PATCH /api/me` | Profil ändern (`display_name?`, `email?`) |
| `POST /api/me/password` | Eigenes Passwort ändern (`current_password`, `new_password`) → `204` |
| `GET /api/quota` | Eigene Quota: `{ limit_bytes, usage_bytes }` |

`UserOut` enthält `id`, `username`, `display_name`, `email`, `is_admin`, `is_active`,
`last_login_at`, `created_at`.

> Nach einer Passwortänderung wird die **Token-Version** des Benutzers erhöht; alle
> bisherigen Tokens sind sofort ungültig (Neuanmeldung erforderlich).

### 8.2 Benutzerverwaltung (nur globaler Admin)

| Methode & Pfad | Beschreibung |
|---|---|
| `GET /api/users?q=&active=&admin=&limit=&offset=` | Liste als `{ items, total, limit, offset }` |
| `POST /api/users` | Anlegen (`username`, `password` (min. 8), `email?`, `display_name?`, `is_admin?`) → `201` |
| `GET /api/users/{id}` | Detail inkl. `groups` |
| `PATCH /api/users/{id}` | Ändern (`display_name?`, `email?`, `is_admin?`) |
| `POST /api/users/{id}/password` | Passwort zurücksetzen (`password`) → `204` |
| `POST /api/users/{id}/activate` · `/deactivate` | Konto aktivieren/deaktivieren → `204` |
| `POST /api/users/bulk/active` | Mehrere aktivieren/deaktivieren: `{ ids: [...], active: bool }` → `{ ok, failed }` |
| `POST /api/users/bulk/delete` | Mehrere löschen: `{ ids: [...], transfer_to? }` → `{ ok, failed }` (letzter Admin geschützt) |
| `DELETE /api/users/{id}?transfer_to={uuid}` | Endgültig löschen; Besitzübertragung nötig, falls der Benutzer Dateien besitzt |
| `GET /api/users/{id}/groups` | Gruppen des Benutzers |
| `POST /api/users/{id}/groups/{group_id}` · `DELETE …` | Gruppe zuweisen/entfernen → `204` |

**Regeln:** Der **letzte aktive Administrator** kann nicht deaktiviert oder gelöscht
werden (`409`). Deaktivieren invalidiert alle Tokens des Benutzers. Besitzt der Benutzer
Dateien, verlangt das Löschen `transfer_to` (`409` sonst).

### 8.3 Gruppen, ACLs, Audit

| Methode & Pfad | Beschreibung |
|---|---|
| `GET /api/groups` · `POST /api/groups` | Gruppen auflisten/anlegen (`name`) |
| `POST /api/groups/{group_id}/members` | Mitglied hinzufügen (`user_id`) → `204` |
| `POST /api/entries/{entry_id}/acl` | ACL-Eintrag setzen (`principal_type`, `principal_id`, `perms[]`) → `201` |
| `GET /api/audit?limit=&offset=&action=` | Audit-Log (neueste zuerst, max. 500); enthält `actor_username` |
| `GET /api/system/stats` | Admin-Monitoring: CPU, RAM, Speicherplatz, Netzwerk-Raten (↑/↓), Laufzeit, IFS-Zähler und SeaweedFS-Volumes |

`GET /api/system/stats` liefert nur für Admins Werte. CPU- und Netzwerk-Raten werden
aus der Differenz zweier Aufrufe gebildet (der erste Aufruf liefert `null`).
Die WebUI stellt CPU, RAM und Netzwerk als Verlaufskurven mit einstellbarem Abfrage-
Intervall (2/5/10/30 s) dar.

```json
{
  "cpu": { "cores": 2, "percent": 10.9, "load_1": 2.19, "load_5": 1.8, "load_15": 1.4 },
  "memory": { "total": 1942544384, "available": 814764032, "used": 1127780352, "percent": 58.1 },
  "disk": { "path": "/", "total": 57644589056, "used": 20114882560, "free": 34568318976, "percent": 34.9 },
  "network": { "rx_bytes_per_sec": 775.6, "tx_bytes_per_sec": 905.3, "rx_total": 14825, "tx_total": 12095 },
  "uptime_seconds": 243019.7,
  "ifs": { "users": 1, "entries": 8, "stored_bytes": 0 },
  "seaweedfs": { "volumes": 14, "files": 1, "used_bytes": 1048 }
}
```

`perms` sind eine Liste aus `read`, `write`, `delete`, `share`, `admin`.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"geheim123","email":"alice@example.org"}' \
  localhost:8000/api/users

curl -s -H "Authorization: Bearer $TOKEN" "localhost:8000/api/users?q=ali&active=true"

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"principal_type":"group","principal_id":"<group-id>","perms":["read","write"]}' \
  localhost:8000/api/entries/<entry-id>/acl
```

Ausführlich: [Tutorial 5](../tutorials/05-benutzer-gruppen-rechte.md).

### 8.4 Rollen

Rollen sind benannte Rechtebündel mit `scope` **`system`** (global) oder **`resource`**
(auf Einträge, vererbt sich auf Nachfahren). Eingebaute Rollen sind schreibgeschützt.

| Methode & Pfad | Beschreibung |
|---|---|
| `GET /api/roles?scope=` | Rollen auflisten |
| `POST /api/roles` | Anlegen (`name`, `scope`, `permissions[]`, `description?`) → `201` |
| `GET/PATCH/DELETE /api/roles/{id}` | Lesen/ändern/löschen (eingebaute: `409` beim Ändern der Rechte/Löschen) |
| `GET /api/roles/{id}/assignments` | Zuweisungen der Rolle |
| `POST /api/roles/{id}/assignments` | Zuweisen (`principal_type`, `principal_id`, `entry_id?`) → `201`; bei gesetztem `entry_id` ist `admin` am Eintrag erforderlich (`403` sonst) |
| `DELETE /api/roles/assignments/{id}` | Zuweisung entfernen → `204`; bei Entry-gebundenen Zuweisungen ist zusätzlich `admin` am Eintrag erforderlich (`403` sonst) |
| `GET /api/entries/{id}/roles` | Rollen eines Eintrags (Recht `admin` am Eintrag) |
| `POST /api/entries/{id}/roles` | Ressourcenrolle zuweisen (`role_id`, `principal_type`, `principal_id`) |
| `DELETE /api/entries/{id}/roles/{assignment_id}` | Eintragsrollen-Zuweisung entfernen |
| `GET /api/users/{id}/roles` · `POST …/{role_id}` · `DELETE …/{assignment_id}` | Systemrollen eines Benutzers |

`permissions` sind `read`, `write`, `delete`, `share`, `admin`; zusätzlich liefert die
API `permission_names`.

**Eingebaute Rollen:** `admin`, `auditor`, `user` (Scope `system`);
`viewer`, `collaborator`, `editor`, `sharer`, `manager` (Scope `resource`).

Die systemweite Rolle `admin` wirkt wie das Admin-Flag – auch für Admin-Endpunkte. Das
bestehende ACL-Modell (`POST /api/entries/{id}/acl`) bleibt unverändert nutzbar.

Beispiel:

```bash
# Ressourcenrolle "viewer" an Gruppe auf einem Ordner vergeben
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"role_id":"<role-id>","principal_type":"group","principal_id":"<group-id>"}' \
  localhost:8000/api/entries/<entry-id>/roles
```

---

## 9. System

| Pfad | Beschreibung |
|---|---|
| `GET /healthz` | Liveness/Readiness; `{"status":"ok"}` (ohne `/api`-Präfix) |
| `GET /version` | Name, Version (bewusst ohne Umgebungsangabe) |
| `GET /metrics` | Prometheus-Textmetrics |
| `GET /docs` | Swagger UI |
| `GET /` | Web-UI |

---

## 10. Berechtigungsmodell in Kürze

| Aktion | benötigtes Recht |
|---|---|
| Auflisten/Lesen/Download | `read` |
| Anlegen/Upload/Umbenennen (Ziel) | `write` |
| Kopieren | `read` + `share` auf der Quelle, `write` auf dem Ziel |
| Verschieben | `write` auf Quelle und Ziel, zusätzlich `read` auf der Quelle |
| Löschen | `delete` |
| Freigabe erstellen | `read` + `share` |
| Freigabe widerrufen | `share` (Ersteller, `share`-Berechtigte oder Systemadmin) |
| Freigabe ändern (PATCH) | Ersteller oder `share`; kein Admin-Override |
| Veröffentlichen (Galerie) | `read` + `share` |
| Veröffentlichung zurückziehen | Ersteller, `share`-Berechtigte oder Systemadmin |
| ACL setzen | `admin` am Eintrag |
| Ressourcenrolle zuweisen (`entry_id`) | `admin` am Eintrag |
| Benutzer/Gruppen/Audit | globale Adminrolle |

Der **Besitzer** eines Eintrags hat implizit alle Rechte. Rechte werden entlang des
Baums vererbt. Details: [Sicherheit](sicherheit.md).
