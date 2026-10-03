# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

## [Unreleased]

### Performance
- **FTPS-Durchsatz deutlich erhöht** – ohne Änderung des nach außen sichtbaren
  Verhaltens:
  - **Download:** Das Gateway streamt S3-Objekte mit Read-Ahead
    (`IFS_FTP_S3_READAHEAD_BYTES`, Standard 4 MiB) über einen fortlaufenden
    Range-Request. Zuvor öffnete es für jeden 64-KiB-Block, den pyftpdlib liest,
    einen eigenen signierten S3-Request; bei großen Dateien sinkt die Zahl der
    Round-Trips damit von einem pro Block auf einen pro Read-Ahead-Bereich.
  - **Upload:** Das Größenlimit wird über einen reinen Byte-Zähler statt per
    `fstat`-Syscall je Schreibblock geprüft; `APPE`/`REST` übernehmen die bereits
    vorhandene Dateigröße einmalig.
  - **Datenkanal:** Socket-Puffer und Datei-Producer werden über
    `IFS_FTP_TRANSFER_BUFFER_BYTES` (Standard 256 KiB, Minimum 16 KiB)
    gebündelt, was die Zahl der Syscalls und TLS-Records je Transfer senkt.
  - **S3-Connection-Pool:** `IFS_S3_MAX_POOL_CONNECTIONS` (Standard 32) hebt das
    botocore-Limit von 10 an, damit parallele FTPS-/HTTP-Transfers sich nicht
    serialisieren.

### Added
- Neue Durchsatz-Einstellungen `IFS_FTP_TRANSFER_BUFFER_BYTES` und
  `IFS_FTP_S3_READAHEAD_BYTES` (FTPS) sowie `IFS_S3_MAX_POOL_CONNECTIONS` (S3).
  Details unter [Konfiguration](docs/konfiguration.md) und
  [FTPS-Handbuch](docs/ftps.md).

## [0.1.3] – 2026-10-03

### Security
- **Veröffentlichungen:** Anonyme Downloads der öffentlichen Galerie sind pro IP+Eintrag
  rate-limitiert (`429`/`Retry-After`); fremde Veröffentlichungen werden Systemadmins
  ohne `path` gemeldet; die eigene Wurzel ist nicht veröffentlichbar (`400`); beim
  erneuten Veröffentlichen wird der ursprüngliche Ersteller korrekt als
  `published_by_username` gemeldet; die Galerie-Suche behandelt `%`, `_` und `\` als
  Literale. Die öffentliche Anzeige des Benutzernamens des Veröffentlichenden bleibt ein
  bewusst akzeptiertes Restrisiko.
- **Release-Prozess:** Getrennte `build`- (nur Lesen) und `release`-Jobs (Schreiben,
  Environment `release`), alle Actions auf Commit-SHAs gepinnt; `SHA256SUMS` und
  Build-Provenance werden veröffentlicht, die Toolchain exakt gepinnt; der Release
  verifiziert den Tag auf `main` und verhindert parallele Läufe; `ci.yml` erhält
  `contents: read`; kein pip-Cache im Release-Build; Gitleaks-Secret-Scan in der CI.

### Added
- **Veröffentlichungen (öffentliche Galerie):** Dateien und Ordner können ausdrücklich
  veröffentlicht werden und erscheinen dann ohne Anmeldung unter der neuen, teilbaren
  Seite `/published`. Ordner werden beim Download als ZIP geliefert; Dateien mit
  Range-Support. Dort ist zudem eine Namensfilterung möglich. Veröffentlichungen werden
  über `POST/DELETE /api/published/{id}` verwaltet,
  in der App unter „Veröffentlichungen“ aufgelistet und beim Löschen eines Eintrags
  sowie beim Deaktivieren/Löschen eines Kontos automatisch zurückgezogen.
  Neue Migration `migrations/sql/0008_published_entries.sql`.
- **Testlauf deutlich schneller:** Das Schema wird nur noch einmal je Worker
  angelegt und zwischen den Tests geleert, Argon2 läuft in Tests mit minimalen
  Parametern, der S3-Bucket-Check des App-Starts entfällt und SQLite schreibt
  ohne `fsync`. Zusätzlich ist `pytest-xdist` verfügbar
  (`python -m pytest -n auto`). Laufzeit der Suite: ~228 s → ~26 s seriell,
  ~22 s parallel (auf 4 Kernen).

## [0.1.2] – 2026-10-03

### Performance
- **Parallel-Optimierung über API, Kern, FTPS, Web-UI und Worker** – ohne Änderung des
  nach außen sichtbaren Verhaltens. Gemessene Beispiele (Testsystem/SQLite):
  - **Authz (PDP):** Rechteprüfung in Baumtiefe 20: 62 → 4 Queries (Folgeaufrufe je
    Session 0); Login/`/me` je 1 SELECT statt 2.
  - **Namespace:** Teilbaum-Operationen ebenenweise statt pro Knoten
    (`subtree_stats` 63 → 3; `path_of` Tiefe 25: 26 → 1; `soft_delete` Breite 60: 65 → 5).
  - **FTPS:** `LIST`/`MLSD` ohne Stat-Query je Eintrag (~14 → 0 Zusatz-Statements/Eintrag;
    60 Einträge: ~1,2 s → ~0,05 s).
  - **Uploads:** kein doppelter Voll-Hash mehr (Simple-Upload 3 → 2 Datei-Pässe,
    Resumable-Complete 2 → 1 Hashlauf); `sha256_file` allokationsfrei per `readinto`.
  - **Listen/Admin:** `GET /entries` ohne Pfad-N+1, Benutzer-/Rollen-Listen ohne
    Gruppen-N+1, Systemmetriken 3 → 1 Abfrage (mit kurzem TTL-Cache beim Polling).
  - **Web-UI:** Polling nur bei sichtbarem Panel, `AbortController` und
    In-Flight-Deduplizierung, entprellte Eingaben, Aufräumen von Objekt-URLs/Timern.
  - **Plattform:** `normalize_path` ~2× schneller; Security-Header als reine
    ASGI-Middleware (~90 % weniger Overhead); Worker-Intervall/-Batch über
    `IFS_WORKER_INTERVAL_SECONDS`/`IFS_WORKER_OUTBOX_BATCH_SIZE` justierbar
    (Defaults unverändert 10 s/200).
- **Datenbank:** Neue Migration `migrations/sql/0007_performance_indexes.sql` ergänzt zehn
  zusammengesetzte Indizes (u. a. `entries(parent_id, trashed_at)`,
  `versions(entry_id, seq)`, `role_assignments(principal_type, principal_id, entry_id)`)
  und ersetzt fünf dadurch abgedeckte Einzelindizes. Bei Bestandsinstallationen die
  Migration anwenden.
- **Tests:** 116 neue Tests, u. a. Query-Zähler-Nachweise für die optimierten Pfade –
  Gesamtsuite jetzt 290 Tests.

### Changed
- **Deploy/Build beschleunigt:** Das `Dockerfile` installiert die Laufzeit-Abhängigkeiten
  zuerst in einer eigenen, cache-fähigen Schicht (`requirements.txt`, BuildKit-Pip-Cache)
  und bindet den Code über `PYTHONPATH=/app/src` ein – reine Code-/UI-Änderungen lösen
  damit **keine** pip-Installation mehr aus. `docker-compose.yml` nutzt ein **gemeinsames
  Image** (`ifs-local:latest`) für `api`/`ftp`/`worker`. Gemessen auf dem Testsystem:
  Build nach reiner Code-Änderung **~135 s → ~3 s**; `requirements.txt` wird durch
  `tests/test_packaging.py` mit `pyproject.toml` synchron gehalten.

### Fixed
- **Web-UI:** Einzelnes **„Verschieben"** im Zeilenmenü des Dateimanagers
  wiederhergestellt (der Endpunkt `POST /api/entries/{id}/move` war unverändert
  vorhanden). Die Zielauswahl schließt den Eintrag selbst und seinen Teilbaum aus;
  mehrfaches Verschieben über die Bulk-Leiste bleibt erhalten.

## [0.1.1] – 2026-09-29

### Fixed
- **FTP:** Downloads > 64 KiB wurden still abgeschnitten (`RETR`/`REST`); der
  S3-Range-Reader lädt jetzt bis zur Blob-Größe nach. Domänenfehler bei
  `MKD`/`RMD`/`DELE`/`RENAME`/`MFMT`/`SITE CHMOD` liefern „550“ statt eines
  Verbindungsabbruchs; abgebrochene bzw. wiederholte Uploads räumen ihren Spool auf;
  Zeitstempel werden als UTC interpretiert.
- **Uploads:** `413` bricht die resumable Session jetzt dauerhaft ab (Commit statt
  Rollback); vor jedem Chunk wird der Spool auf den bestätigten Offset gekürzt und
  beim Abschluss gegen den Offset geprüft – Client-Abbrüche erzeugen keine doppelten
  oder zu kurzen Dateien mehr. Upload-Sessions sind strikt besitzergebunden.
- **Namespace:** Ordner-Kopien enthalten jetzt den vollständigen aktiven Teilbaum;
  die Quota wird beim Überschreiben fremder Dateien dem ursprünglichen Besitzer
  angerechnet und beim Versions-Rollback geprüft; `Last-Modified` nutzt UTC.
- **Freigaben:** Bei `overwrite=false` können parallele Drop-Link-Uploads keine
  vorhandene Datei mehr ersetzen (TOCTOU); Widerrufe werden als `share.revoke`
  auditiert; negative `max_downloads`/`size` werden mit `422` abgelehnt.
- **Rollen:** Entry-gebundene Zuweisungen lassen sich nur noch mit `admin` am Eintrag
  entfernen.
- **Archiv:** Abgebrochene/abgelaufene ZIP-Jobs hinterlassen keine Spool-Dateien;
  ZIP-Ausweichnamen kollidieren nicht mehr.
- **Content/Login:** CAS-Dedup aktualisiert vorhandene Blob-Zeilen statt sie zu
  duplizieren; ein erfolgreicher Login setzt den IP-weiten Fehlversuchszähler nicht
  mehr zurück.
- **Web-UI:** Nicht-OK-Antworten werden nicht mehr als Erfolg angezeigt; der
  „Zugriff“-Dialog ist auf Systemadmins beschränkt; Logout räumt Auswahl, ZIP-Poll
  und Modals auf; mehrere Anzeigefehler behoben.
- **Tests:** 52 neue Regressionstests (`tests/test_review5_*.py`); Gesamtsuite 173 Tests.

## [0.1.0] – 2026-09-29

Erster öffentlicher **Pre-Release**. Funktionsumfang des MVP einschließlich
mehrerer abgeschlossener Sicherheits-Reviews.

### Added
- **HTTP(S)-API** mit JWT-Login, Verzeichnis-/Dateioperationen (auflisten, anlegen,
  umbenennen, verschieben, kopieren, löschen), Download mit `Range`/`HEAD`/ETag/Streaming.
- **Uploads**: einfacher `PUT` sowie resumable Sessions (tus-ähnlich, `POST`/`PATCH`/`complete`).
- **FTPS** (explizites TLS) mit demselben Benutzer-/Rechtebestand, `LIST`/`NLST`/`MLSD`,
  `RETR` (resumable), `STOR`/`APPE`, `MKD`/`RMD`/`DELE`/`RNFR`.
- **Rechte-/Rollenmodell**: zentrales Autorisierungsmodul (PDP) mit Vererbung, ACLs,
  System- und Ressourcenrollen – identisch für HTTP und FTPS.
- **Account-Manager**: Benutzer anlegen/bearbeiten, Passwort-Reset, deaktivieren,
  Gruppen, Selbstbedienung (Profil/Passwort), Schutz des letzten Admins.
- **Papierkorb** mit Wiederherstellung, endgültigem Löschen und Batch-Aktionen.
- **Interne Freigaben und externe Links** (Passwort, Ablauf, Downloadlimit,
  Ordner-Freigaben als ZIP, Drop-Upload mit optionalem Überschreiben).
- **Bulk-Aktionen** (Sammel-Download, Verschieben, Löschen) und Batch-Aktionen für
  Benutzer und Freigaben.
- **Versionierung** je Datei mit Download und Rollback.
- **Web-UI** (Dateimanager) mit Drag-&-Drop-Upload, Resumable-Upload mit Fortschritt,
  ZIP-Download für Ordner mit (minimierbarer) Fortschrittsanzeige, Vorschau für
  Text/Code, HTML, PDF, Bilder, Video und Audio.
- **Per-User-Wurzel (Home)**: Isolation der Benutzer; Systemadmins haben keinen
  Dateizugriff auf fremde Wurzeln; Ansicht „Mit mir geteilt".
- **Admin-Monitoring** (CPU/RAM/Netzwerk-Verläufe, Speicherplatz, SeaweedFS-Volumes)
  und **Audit-Log-Ansicht**.
- **Betrieb**: Docker Compose mit lokalem SeaweedFS (S3-API) oder externem S3,
  Worker für Events/Papierkorb/Blob-GC, Health-/Metrik-Endpunkte.

### Security
- Gehärtete Konfiguration (JWT-Prüfung, Produktions-Validierung, Security-Header,
  Rate-Limiting für Login und Freigabe-Passwörter).
- Scoped Tokens (Vorschau/Archiv) mit `token_version`-Invalidierung.
- Sicherheits-Reviews 1–4 umgesetzt, u. a.: Autorisierung bei Wiederherstellung
  (nur oberste Papierkorb-Einträge, `read`-Prüfung), aktive Inhalte nur als Attachment,
  Share-Passwörter per Header, eindeutige Dateinamen je Ordner, atomare Limits.
- Per-User-Isolation: keine Sicht auf fremde Wurzeln, auch nicht als Systemadmin;
  Rollen-/ACL-Vergabe erfordert `admin` am Eintrag.
- Review 4 (S1–S4): `POST /api/shares` verlangt `read`+`share`; Rollenzuweisung mit
  `entry_id` erfordert `admin` am Eintrag; `PATCH` fremder Freigaben gesperrt
  (Widerrufen bleibt möglich); FTP-Vorladen prüft `read`; strengere Feldvalidierung.

### Known limitations
- Kein Multi-Tenant-Betrieb.
- Echtes E2E/Zero-Knowledge (Client-Verschlüsselung) ist noch nicht umgesetzt
  (siehe „Per-User-Wurzel" in `FEATURES.md`); Server-Features wie FTPS/Vorschau/ZIP
  verarbeiten Inhalte derzeit serverseitig.
- Login-Rate-Limit ist pro Prozess (In-Memory); für den vorgesehenen Maßstab
  (eine API-Instanz) ausgelegt.

[Unreleased]: https://github.com/themistermorph/inproper-file-service/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/themistermorph/inproper-file-service/releases/tag/v0.1.3
[0.1.2]: https://github.com/themistermorph/inproper-file-service/releases/tag/v0.1.2
[0.1.1]: https://github.com/themistermorph/inproper-file-service/releases/tag/v0.1.1
[0.1.0]: https://github.com/themistermorph/inproper-file-service/releases/tag/v0.1.0
