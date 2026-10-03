# IFS – Webbasiertes Dateisystem

[![CI](https://github.com/themistermorph/inproper-file-service/actions/workflows/ci.yml/badge.svg)](https://github.com/themistermorph/inproper-file-service/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](pyproject.toml)

> **Status: `0.1.5` - Pre-Release.** Der Funktionsumfang ist für den internen Betrieb
> geeignet; API und Datenmodell können sich bis `1.0.0` noch ändern.
> Änderungen: [`CHANGELOG.md`](CHANGELOG.md) · Release-Anleitung: [`RELEASING.md`](RELEASING.md).

Ein zentrales Dateisystem mit **HTTP(S)-** und **FTPS**-Zugriff auf einen
S3-kompatiblen Objektspeicher. Der Namespace (Ordner, Dateien, Rechte) liegt in
PostgreSQL, die Inhalte als unveränderliche, content-addressed Blobs in S3.

Grundlage und Detailkonzept: [`ANFORDERUNGEN.md`](ANFORDERUNGEN.md) und
[`ARCHITEKTUR.md`](ARCHITEKTUR.md).

## Dokumentation

- **Handbücher** – Einstieg über [`docs/README.md`](docs/README.md):
  Installation, Konfiguration, API-Referenz, FTPS, Datenmodell, Sicherheit, Betrieb,
  Entwicklung.
- **Featurebeschreibung Ausbaustufe 2** – [`FEATURES.md`](FEATURES.md):
  Account-Manager, Papierkorb, Rollenmanagement und weitere Vorschläge.
- **Tutorials** – Schritt-für-Schritt ab [`tutorials/01-schnellstart.md`](tutorials/01-schnellstart.md):
  Schnellstart, HTTP-Dateien, FTPS, resumable Uploads, Rechte, Freigaben,
  Veröffentlichungen (öffentliche Galerie), Backup, API-Anbindung,
  Produktiv-Deployment, Troubleshooting.

---

## Funktionsumfang (MVP)

- **HTTP(S)**: Login (JWT), Verzeichnisse/Dateien auflisten, anlegen, umbenennen,
  verschieben, kopieren, löschen
- **Download**: `GET`/`HEAD` mit `Range`-Support (resumable, `206`), ETag, Streaming
- **Upload**: einfacher `PUT` und resumable Session (tus-ähnlich) über `POST`/`PATCH`
- **FTPS**: explizites TLS, Login gegen denselben Benutzer-/Rechtebestand,
  `LIST`/`NLST`/`MLSD`, `RETR` (resumable per `REST`), `STOR`/`APPE`, `MKD`/`RMD`/`DELE`/`RNFR`
- **Rechte**: zentrales Autorisierungsmodul (PDP), Vererbung entlang des Baums,
  identisch für HTTP und FTPS
- **Verwaltung**: Benutzerverwaltung (anlegen, bearbeiten, Passwort-Reset, deaktivieren,
  Gruppen), Gruppen, ACLs, Audit-Log
- **Selbstbedienung**: eigenes Profil und Passwort ändern
- **Rollen**: benannte Rechtebündel (systemweit oder pro Eintrag) – ergänzen die ACLs
- **Freigaben**: Übersicht aller Links mit Kopieren, Öffnen und Widerrufen; Dateien und **Ordner** (als ZIP), optional Upload per Drop-Link
- **Veröffentlichungen**: Dateien und **Ordner** (als ZIP) ausdrücklich in einer **öffentlichen Galerie** (`/published`, ohne Login, inkl. Download) bereitstellen und jederzeit zurückziehen
- **Bulk-Aktionen**: Mehrfachauswahl zum Sammel-Download (.zip), Verschieben und Löschen
- **Versionen**: Historie je Datei mit Download und Rollback
- **Audit-Log**: Admin-Ansicht der Ereignisse mit Filter
- **Monitoring**: Admin-Panel mit Verlaufskurven für CPU, RAM und Netzwerk (↑/↓), einstellbarem Intervall sowie Speicherplatz- und SeaweedFS-Volumes-Statistik
- **Papierkorb**: gelöschte Einträge auflisten, wiederherstellen oder endgültig entfernen; Mehrfachauswahl für Batch-Aktionen
- **Per-User-Wurzel**: jeder Benutzer hat ein eigenes Home; Systemadmins haben **keinen** Zugriff auf fremde Dateien; freigegebene Einträge erscheinen unter „Mit mir geteilt"
  (Aufbewahrungsfrist konfigurierbar)
- **Betrieb**: Docker Compose, Worker für Events/Papierkorb/Blob-GC, Health/Metriken
- **Web-UI**: Dateimanager unter `/` (Browsen, Ordner, Drag-&-Drop-Upload mit Fortschritt,
  resumable Upload, Download, **ZIP-Download für Ordner mit minimierbarer Fortschrittsanzeige**,
  **Verschieben** (einzeln und mehrfach), Umbenennen, Löschen, Details,
  Freigabelinks, **Veröffentlichungen** (öffentliche Galerie unter `/published`),
  **Vorschau** für Text/Code, HTML, PDF, Bilder, Video und Audio)

---

## Schnellstart mit Docker Compose

Voraussetzung: Docker + Docker Compose. Objektspeicher ist **lokal (SeaweedFS)** – optional
kann stattdessen ein externer S3-kompatibler Speicher genutzt werden.

```bash
cp .env.example .env
# In .env setzen: IFS_JWT_SECRET und IFS_ADMIN_PASSWORD
bash deploy/setup-seaweedfs.sh   # erzeugt S3-Zugangsdaten + Storage-UI-Login
docker compose up -d --build
bash deploy/preflight-s3.sh      # prüft S3 (Head/Put/Get/Range/Multipart)
```

Danach erreichbar:

| Dienst | Adresse |
|---|---|
| Web-UI / API | http://localhost:8000 |
| FTPS | `ftps://localhost:21` (explizites TLS) |
| Storage-UI (nur Admins, Basic Auth) | http://localhost:9010 |

Beim ersten Start wird – sofern `IFS_ADMIN_PASSWORD` gesetzt ist – ein Admin und dessen
**Home (Wurzel)** angelegt; jeder neu angelegte Benutzer erhält automatisch ein eigenes
Home (Per-User-Wurzel).

> **Objektspeicher:** Standard ist **SeaweedFS** als lokaler S3-kompatibler Dienst mit
> persistentem Volume `s3-data`. Für externes S3 (AWS/Ceph/Wasabi) einfach
> `IFS_S3_ENDPOINT_URL`/Keys in `.env` setzen und den `s3`-Service weglassen.
> Verwaiste Datei-Einträge (fehlende Blobs) bereinigen:
> `docker compose exec -T api python -m ifs.cli cleanup-missing-blobs`.

### FTPS-Zertifikat erzeugen

FTPS benötigt ein Zertifikat. Für die Entwicklung:

```bash
python scripts/gen-dev-cert.py certs
```

Erzeugt `certs/ftps.crt` und `certs/ftps.key`, die Compose nach `/etc/ifs` mountet.
Im Produktivbetrieb ein echtes Zertifikat verwenden.

> **Wichtig:** FTPS lässt sich nicht über einen HTTP-Reverse-Proxy terminieren.
> Der Dienst muss per TCP erreichbar sein (Port 21 plus die feste Passive-Portrange,
> Standard 30000–30100). Hinter NAT/Load-Balancer – und **zwingend im Docker-Betrieb** –
> `IFS_FTP_MASQUERADE_ADDRESS` auf die von außen erreichbare Adresse setzen. Sonst
> meldet `PASV` die Container-IP (z. B. `172.18.0.5`) und der Datenkanal läuft ins
> Timeout („Datenverbindung zeitüberschritten“).

---

## Lokale Entwicklung

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS

# Für Tests genügt SQLite (siehe tests/conftest.py); für die App Postgres + S3 starten.
.venv\Scripts\python -m ifs.cli init-db      # Schema anlegen + Bucket prüfen
.venv\Scripts\python -m ifs.cli run-api      # HTTP-API + Web-UI auf :8000
.venv\Scripts\python -m ifs.cli run-ftp      # FTPS-Gateway (benötigt Zertifikat)
.venv\Scripts\python -m ifs.cli run-worker   # Hintergrund-Worker
```

Tests:

```bash
.venv\Scripts\python -m pytest                  # parallel (Standard, pytest-xdist)
.venv\Scripts\python -m pytest -n0              # seriell (z. B. zum Debuggen)
.venv\Scripts\python -m ruff check src tests
```

---

## Konfiguration

Alle Einstellungen über Umgebungsvariablen mit Präfix `IFS_` (siehe `.env.example`).
Die wichtigsten:

| Variable | Bedeutung | Standard |
|---|---|---|
| `IFS_DATABASE_URL` | SQLAlchemy-URL | `postgresql+psycopg://ifs:ifs@localhost:5432/ifs` |
| `IFS_DB_PASSWORD` | DB-Passwort (von Compose zur URL zusammengesetzt) | `ifs` |
| `IFS_AUTO_CREATE_SCHEMA` | Schema beim Start anlegen (Dev) | `false` |
| `IFS_JWT_SECRET` | Schlüssel für Access-Tokens (min. 32 Zeichen; in Produktion Pflicht) | *(leer)* |
| `IFS_S3_ENDPOINT_URL` | S3-Endpunkt (leer = AWS) | – |
| `IFS_S3_BUCKET` | Bucket für Blobs | `ifs` |
| `IFS_S3_SSE` | Serverseitige Verschlüsselung | `AES256` |
| `IFS_UPLOAD_SPOOL_DIR` | Temp-Verzeichnis für Uploads | `/var/lib/ifs/spool` |
| `IFS_MAX_UPLOAD_SIZE` | Max. Dateigröße in Bytes (0 = ∞) | `0` |
| `IFS_SHARE_UPLOAD_MAX_SIZE` | Max. Größe öffentlicher Drop-Link-Uploads (0 = ∞) | `104857600` |
| `IFS_SHARE_UPLOAD_MAX_REQUESTS` | Rate-Limit öffentliche Share-Uploads (pro Freigabe+IP) | `60` |
| `IFS_PUBLISHED_DOWNLOAD_MAX_REQUESTS` | Rate-Limit anonymer Galerie-Downloads (pro Eintrag+IP) | `120` |
| `IFS_PUBLISHED_DOWNLOAD_WINDOW_SECONDS` | Zeitfenster für `IFS_PUBLISHED_DOWNLOAD_MAX_REQUESTS` | `300` |
| `IFS_FTP_PASSIVE_PORTS` | Passive-Portrange | `30000-30100` |
| `IFS_FTP_MASQUERADE_ADDRESS` | nach außen sichtbare FTPS-Adresse (Docker/NAT: zwingend) | – |
| `IFS_MONITOR_DISK_PATH` | Pfad für die Speicherbelegung im Admin-Panel | `/` |
| `IFS_SEAWEEDFS_STATUS_URL` | SeaweedFS-Volume-Status fürs Admin-Panel | *(leer)* |

---

## API-Auszug

```
POST   /api/auth/login                 Login → Bearer-Token
GET    /api/me

GET    /api/entries?parent_id=...      Verzeichnis auflisten
GET    /api/shared                     mit mir geteilte Einträge
GET    /api/root                       eigene Wurzel (Home)
POST   /api/folders                    Ordner anlegen
PATCH  /api/entries/{id}               umbenennen
POST   /api/entries/{id}/move          verschieben
POST   /api/entries/{id}/copy          kopieren
DELETE /api/entries/{id}               in den Papierkorb
GET    /api/trash                      Papierkorb auflisten
POST   /api/trash/{id}/restore         wiederherstellen
POST   /api/trash/bulk/restore         mehrere wiederherstellen
POST   /api/trash/bulk/purge           mehrere endgültig löschen
DELETE /api/trash/{id}                 endgültig löschen
DELETE /api/trash                      Papierkorb leeren
GET    /api/entries/{id}/content       Download (Range → 206)
PUT    /api/uploads/simple             einfacher Upload
POST   /api/uploads                    Upload-Session eröffnen (resumable)
PATCH  /api/uploads/{id}               Chunk anhängen (Header Upload-Offset)
POST   /api/uploads/{id}/complete      abschließen
POST   /api/archive/jobs               ZIP-Erstellung starten (Fortschritt)
GET    /api/archive/jobs/{token}       ZIP-Fortschritt abfragen
DELETE /api/archive/jobs/{token}       ZIP-Auftrag abbrechen
GET    /api/archive/{token}            fertiges ZIP herunterladen
POST   /api/shares                     Freigabelink erstellen
GET    /api/shares/{token}/content     öffentlicher Download (Ordner als ZIP)
GET    /api/shares/{token}/content/NAME  Download mit Dateiname (curl -O)
POST   /api/shares/{token}/upload      öffentlicher Upload in Ordner (allow_upload)
POST   /api/shares/bulk/revoke         mehrere Freigaben widerrufen
GET    /api/published                  öffentliche Galerie (ohne Login)
GET    /api/published/{id}/content     öffentlicher Download (ohne Login)
GET    /api/published/mine             eigene Veröffentlichungen
POST   /api/published/{id}             Datei/Ordner veröffentlichen (public_name)
PATCH  /api/published/{id}             öffentlichen Anzeigenamen ändern
DELETE /api/published/{id}             Veröffentlichung zurückziehen
GET    /api/users?q=...                Benutzer auflisten (Admin)
POST   /api/users                      Benutzer anlegen (Admin)
POST   /api/users/bulk/active          mehrere aktivieren/deaktivieren (Admin)
POST   /api/users/bulk/delete          mehrere löschen (Admin)
PATCH  /api/me                         eigenes Profil ändern
POST   /api/me/password                eigenes Passwort ändern
GET    /api/roles?scope=...            Rollen auflisten (Admin)
POST   /api/roles                      Rolle anlegen (Admin)
POST   /api/entries/{id}/roles         Rolle auf Eintrag vergeben
GET    /api/groups, /api/audit         Administration
GET    /api/system/stats               Admin-Monitoring (CPU/RAM/Disk/Netz)
```

Beispiel:

```bash
# IFS_ADMIN_PASSWORD muss gesetzt sein (siehe .env)
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/entries
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"docs"}' localhost:8000/api/folders
```

---

## Projektstruktur

```
src/ifs/
  main.py            FastAPI-App, Lifespan, Exception-Handler
  cli.py             Rollenstart + init-db/seed-admin
  config.py          pydantic-settings (Präfix IFS_)
  db.py / models.py  SQLAlchemy 2.0 + Datenmodell
  security.py        Argon2, JWT, Tokens
  core/              Protokollunabhängiger Kern
    authz.py         zentrales Autorisierungsmodul (PDP)
    namespace.py     Baum, Per-User-Wurzeln, Versionen, Rename/Move, Papierkorb
    accounts.py      Benutzer/Gruppen/Gruppenverwaltung
    roles.py         Rollen und Zuweisungen
    content.py       S3-Blobs (CAS), Streaming, Presign
    uploads.py       resumable Upload-Sessions
    archive.py / archive_jobs.py  ZIP-Erstellung (synchron/async mit Fortschritt)
    system_stats.py  System-/Container-Metriken fürs Admin-Panel
    quota.py / audit.py / events.py / ratelimit.py
  api/               REST-Endpunkte + Schemas
  ftp/               FTPS-Gateway (pyftpdlib) mit virtuellem Dateisystem
  worker.py          Outbox, Papierkorb, Blob-GC
  web/index.html     Web-Frontend (Dateimanager)
migrations/          Alembic + SQL-Migrationen (sql/000x_*.sql)
scripts/             Hilfsskripte (Dev-Zertifikat, Doku-Link-Checker, Deploy-Helfer)
deploy/              Skripte für das Test-Deployment (SSH/Compose)
docs/                Handbücher; tutorials/ Schritt-für-Schritt-Anleitungen
tests/               pytest (Kern, API, FTPS-End-to-End)
docker-compose.yml       Stack (db, s3, s3-ui, api, ftp, worker) – Standard: lokales SeaweedFS, alternativ externes S3
```

---

## Datenbankmigrationen

Für den schnellen Start legt `ifs init-db` das Schema per `create_all` an. Bestehende
Installationen werden über die SQL-Migrationen unter `migrations/sql/` aktualisiert
(siehe unten). Alembic ist für eigene, autogenerierte Schema-Änderungen vorbereitet:

```bash
alembic revision --autogenerate -m "meine Änderung"
alembic upgrade head
```

> **Bestehende Installation (Upgrade):** Die SQL-Migrationen unter
> `migrations/sql/` nacheinander anwenden, insbesondere
> `0004_unique_entry_names.sql`, `0005_share_overwrite.sql`,
> `0006_per_user_roots.sql`, `0007_performance_indexes.sql` und
> `0008_published_entries.sql`. **Vor** dem
> Unique-Index aus 0006 muss für jeden
> Benutzer eine Wurzel existieren (Per-User-Wurzel), sonst schlägt die Migration
> fehl. Beispiel:
>
> ```bash
> for f in migrations/sql/000*.sql; do
>   docker compose exec -T db psql -U ifs -d ifs < "$f"
> done
> ```

---

## Bewusste MVP-Einschränkungen

- Resumable Uploads spoolen lokal (Temp-Datei) und laden erst beim Abschluss nach S3.
  Für sehr große Dateien ist der direkte S3-Multipart-Upload der nächste Ausbau.
- `STOU` (Store Unique) wird nicht unterstützt.
- Es gibt noch keinen WebDAV-Endpunkt. Versionierungs- und Papierkorb-UI sind vorhanden.
- Der Core ist synchron; die Streaming-Endpunkte laufen als synchrone Aufrufe im
  ASGI-Thread. Bei hoher Nebenläufigkeit wäre ein async-Umbau sinnvoll.
- Kein Virenscanner und keine Volltextsuche (in der Architektur für Stufe 2 vorgesehen).

---

## Mitwirken

Beiträge sind willkommen – siehe [`CONTRIBUTING.md`](CONTRIBUTING.md) und
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Sicherheitslücken bitte **vertraulich**
über [`SECURITY.md`](SECURITY.md) melden.

## Lizenz

Lizenziert unter der **Apache License 2.0** – siehe [`LICENSE`](LICENSE) und
[`NOTICE`](NOTICE).
