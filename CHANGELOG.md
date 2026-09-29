# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

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

[0.1.0]: https://github.com/themistermorph/informal-file-system/releases/tag/v0.1.0
