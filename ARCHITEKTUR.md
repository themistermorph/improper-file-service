# Architekturkonzept – Webbasiertes Dateisystem (IFS)

> Status: Entwurf / Diskussionsgrundlage
> Grundlage: `ANFORDERUNGEN.md`, Rahmenbedingungen E1–E4 (internes Team, S3-Objektspeicher, FTPS-only, grüne Wiese)
> Notation: C4-orientiert (Kontext → Container → Komponenten), plus Datenflüsse und ADRs.

---

## 1. Architekturziele und -prinzipien

| # | Prinzip | Bedeutung für die Umsetzung |
|---|---|---|
| P1 | **Ein Kern, mehrere Protokolle** | AuthN, AuthZ, Metadaten, Quota existieren genau einmal. HTTP und FTPS sind austauschbare Adapter. |
| P2 | **Metadaten sind die Wahrheit** | Der Namespace (Ordner/Baum/Rechte) lebt in PostgreSQL, nicht im Objektspeicher. |
| P3 | **Blobs sind unveränderlich** | Objekte werden nie in-place geändert. Neue Version = neues Objekt + atomarer Metadaten-Zeiger. |
| P4 | **Deny by Default** | Jede Operation wird serverseitig autorisiert – protokollunabhängig. |
| P5 | **Transfers müssen wiederaufsetzbar sein** | Range/REST/Multipart als durchgängiges Muster. |
| P6 | **Betriebsarmut** | Ein Deployable (mehrere Rollen), wenig bewegliche Teile – passend zur Teamgröße. |

**Wichtigste Konsequenz aus P2/P3:** Umbenennen, Verschieben und Ordner-Logik sind reine
Metadaten-Operationen. In S3 wird **nichts** kopiert oder verschoben. Das löst das klassische
„S3 kennt keine Ordner“-Problem sauber und macht Rename atomar und O(1).

---

## 2. Systemkontext (C4-Level 1)

```
        ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
        │  Browser     │        │  FTP-Client  │        │  Scanner /   │
        │  (Web-UI)    │        │  (FileZilla, │        │  MFP / CLI / │
        │              │        │  WinSCP, lftp │       │  Skripte     │
        └──────┬───────┘        └──────┬───────┘        └──────┬───────┘
               │ HTTPS                 │ FTPS                  │ HTTPS/FTPS
               ▼                       ▼                       ▼
        ┌────────────────────────────────────────────────────────────┐
        │                     IFS  (Dateisystem)                     │
        └──────┬───────────────┬───────────────┬─────────────────────┘
               │               │               │
               ▼               ▼               ▼
        ┌────────────┐  ┌────────────┐  ┌──────────────┐
        │ IdP / LDAP │  │ S3-Speicher│  │ Mail/Webhook │
        │ (optional) │  │ (Blobs)    │  │ (Events)     │
        └────────────┘  └────────────┘  └──────────────┘
```

**Externe Systeme:** Identitätsanbieter (lokal/LDAP/OIDC), S3-kompatibler Objektspeicher,
Benachrichtigungsdienste, später Virenscanner und Suchindex.

---

## 3. Container-/Komponentensicht (C4-Level 2)

### 3.1 Deployable Units

Empfehlung: **ein Modulith-Binary mit Rollen**. Dasselbe Image startet je nach Rolle
`api`, `ftp` oder `worker`. So bleibt das gemeinsame Kernmodul garantiert identisch,
eine spätere Auftrennung in getrennte Services ist möglich.

```
                         ┌───────────────────────── Ingress ─────────────────────────┐
                         │  HTTPS: Traefik/Caddy (TLS-Terminierung, Routing)         │
                         └───────────────┬───────────────────────────────────────────┘
                                         │ HTTP
                                         ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────┐
 │                          IFS-Binary (mehrere Rollen)                                  │
 │                                                                                       │
 │  ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────────┐    │
 │  │ Rolle: api    │   │ Rolle: ftp    │   │ Rolle: worker │   │  Core (gemeinsam) │    │
 │  │ REST + SPA    │   │ FTPS-Server   │   │ Jobs/Events   │   │  siehe 3.2        │    │
 │  └───────┬───────┘   └───────┬───────┘   └───────┬───────┘   └─────────┬─────────┘    │
 │          └───────────────────┴───────────────────┴─────────────────────┘              │
 └───────────────────────────────────────┬───────────────────────┬───────────────────────┘
                                         │ SQL                    │ S3-API
                                         ▼                        ▼
                                 ┌───────────────┐        ┌──────────────────┐
                                 │ PostgreSQL 16 │        │ S3 (MinIO / AWS  │
                                 │ Metadaten/ACL │        │ / Ceph / Wasabi) │
                                 └───────────────┘        └──────────────────┘
```

**Wichtiger Betriebshinweis FTPS:** FTPS lässt sich **nicht** durch einen HTTP-L7-Proxy
terminieren. Der FTPS-Dienst muss per TCP direkt erreichbar sein (Port 21 + feste
Passive-Portrange, z. B. 30000–30100). Hinter NAT/Load-Balancer muss die nach außen
sichtbare Adresse (`masquerade address`) konfiguriert werden.

### 3.2 Kernmodule (im gemeinsamen Core)

| Modul | Verantwortung |
|---|---|
| **Identity & Access** | Benutzer, Gruppen, Credentials, Login, Sessions, API-Tokens, OIDC/LDAP-Anbindung |
| **Authorization** | Zentrales Policy-Entscheidungsmodul (PDP): prüft jede Operation gegen ACL + Vererbung |
| **Namespace** | Verzeichnisbaum, Einträge (Ordner/Datei), Rename/Move, Papierkorb, **Per-User-Wurzel (Home)** |
| **Content/Blob** | Abstraktion über S3: Stream-Upload/-Download, Multipart, Range, CAS-Verwaltung, GC |
| **Versioning** | Versionshistorie, Rollback, Wiederherstellung (Ausbaustufe) |
| **Quota** | Verbrauch pro Nutzer/Gruppe, Durchsetzung bei Schreiboperationen |
| **Sharing** | Interne Freigaben, externe Links, Upload-Requests |
| **Transfer-Sessions** | Resumable Uploads (HTTP/tus), Fortsetzungspunkte, Idempotenz |
| **Audit & Events** | Revisionssicheres Audit-Log + transaktionaler Outbox für Worker |
| **Search** | Postgres-Volltextsuche (MVP), später externer Index |

---

## 4. Kernkonzepte

### 4.1 Namespace vs. Blob (das zentrale Modell)

```
Namespace (PostgreSQL)                       Blobs (S3)
─────────────────────                        ──────────
/ (Home je Benutzer)
  / Projekte
    / 2026
       report.pdf ─── current_version ──┐
                                        ▼
                                versions (DB) ── blob_id ──►  blobs/cas/sha256/ab/cd…​ (S3)
```

Jeder Benutzer besitzt ein eigenes **Home** (Wurzel: `parent_id IS NULL`, `owner_id = user`);
die Namen `/…` in diesem Dokument sind relativ zu dieser Wurzel zu lesen.

- **`entries`**: Baumknoten (Ordner/Datei) mit Name, Parent, Besitzer.
- **`versions`**: unveränderliche Version einer Datei (Größe, MIME, Hash, Zeit).
- **`blobs`**: der eigentliche Inhalt, **content-addressed** über SHA-256
  (`blobs/cas/<sha256>`), dadurch automatische Deduplikation und Integritätsprüfung.
- S3-Schlüssel haben **keine** Nutzer-Semantik. Die Hierarchie kennt nur die DB.

### 4.2 Atomare Schreiboperation

1. Daten landen als **neuer Blob** in S3 (Temp-Key bzw. Multipart).
2. Nach erfolgreichem Abschluss + Integritätsprüfung: **eine DB-Transaktion**
   legt die neue `version` an und setzt `entries.current_version_id`.
3. Leser sehen entweder die alte oder die neue Version – nie einen Zwischenzustand.
4. Verwaiste Blobs werden asynchron per Garbage Collection entfernt.

### 4.3 Autorisierung

Ein einziger PDP-Aufruf `authorize(principal, action, entry)` wird von **beiden**
Adapter-Schichten (HTTP und FTPS) genutzt. Rechte werden entlang des Baums vererbt
(Gruppen → Ordner → Kind). Ergebnis wird pro Session/Request gecacht, bei Mutationen
invalidiert. Damit ist P1 (protokollunabhängige Rechte) technisch erzwungen.

---

## 5. Datenmodell (Entwurf)

```
users            (id, username, email, password_hash, status, created_at)
groups           (id, name)
group_members    (group_id, user_id)
credentials      (id, user_id, type[password|token], secret_hash, scopes, expires_at)

entries          (id uuid, parent_id uuid NULL, name, type[folder|file], owner_id,
                  current_version_id NULL, trashed_at NULL, trashed_by NULL,
                  original_parent_id NULL, created_at, updated_at,
                  UNIQUE(parent_id, lower(name)) – je Parent;
                  UNIQUE(owner_id) WHERE parent_id IS NULL AND trashed_at IS NULL – genau eine Wurzel/Benutzer)
versions         (id, entry_id, blob_id, seq, size, mime, sha256, created_by, created_at)
blobs            (id, storage_key, size, sha256, status, created_at)
acl              (id, entry_id, principal_type[user|group], principal_id,
                  perms bitmask, inherited)
shares           (token, entry_id, created_by, password_hash, expires_at,
                  max_downloads, downloads, allow_upload, overwrite, created_at)
upload_sessions  (id, entry_id, owner_id, protocol, offset, s3_upload_id,
                  parts jsonb, expires_at, status)
quota_usage      (principal_type, principal_id, bytes, files)
audit_log        (id, ts, actor_id, action, target_entry, protocol, ip, result, details)
outbox           (id, event_type, payload, published_at)
```

**Entwurfsentscheidungen**

- **UUIDv7** als IDs (zeitlich sortierbar, indexfreundlich).
- Eindeutigkeit von Namen case-insensitiv pro Parent.
- `perm`-Bitmaske: `read | write | delete | share | admin`.
- Papierkorb = `trashed_at` (soft delete); GC/endgültiges Löschen asynchron.
- Audit **append-only**, Schreibzugriff nur über Core.
- Events über **Outbox-Pattern** → konsistent mit der jeweiligen Transaktion.

---

## 6. Datenflüsse

### 6.1 HTTP-Download (mit Resume)

```
Client → API: GET /api/entries/{id}/content  (Authorization, Range: bytes=…)
API    → AuthZ: authorize(user, read, entry)          ── deny → 403
API    → Namespace: current_version → blob_key
API    → S3: GET blob (Range)      ──stream──►  Client (200 / 206)
API    → Audit: transfer.download
```

Option für große Dateien: API antwortet `302` auf eine **kurzlebige Presigned URL**
(TTL ~5 min). Vorteil: Datenpfad direkt Client↔S3. `Range` funktioniert nativ.
Nachteil: feingranulares Byte-Audit entfällt (nur Autorisierung wird geloggt).
Empfehlung: Streaming durch den Core als Default (kleine Installation reicht völlig),
Presigned-URLs als konfigurierbares Opt-in für sehr große Dateien.

### 6.2 HTTP-Upload (resumable)

```
1. Client → API: POST /api/uploads {parentId, name, size, sha256}
   API → AuthZ (write) + Quota-Check → upload_session + S3-Multipart-Init
2. Client → API: PATCH /api/uploads/{id}  (tus, Content-Range)   [oder presigned Parts]
   API → S3: Multipart-Upload der Chunks
3. Client → API: POST /api/uploads/{id}/complete
   API → Integritätsprüfung (Größe, SHA-256), ggf. Virenscan
   API → DB-Tx: version anlegen + current_version setzen  (atomar)
4. Antwort: neuer entry (201)
```

### 6.3 FTPS-Download / -Upload

```
Download:  Client ──AUTH TLS/USER/PASS──► FTP-Gateway → Core.AuthN
           RETR pfad  → Core: Pfad auflösen + AuthZ(read)
           DATA (TLS) ←── Gateway streamt S3-Objekt (REST-Offset = Range-GET)
Upload:    STOR pfad  → AuthZ(write) + Quota
           DATA (TLS) ──► Gateway streamt in S3-Multipart (Temp-Key)
           Abschluss → Integrität → DB-Tx (wie 6.2)
Resume:    REST (Download-Offset), APPE (Anhängen) – siehe ADR-6
```

FTP-Clients kennen kein S3; deshalb **streamt das FTP-Gateway immer über den Core**,
Presigned-URLs sind hier nicht anwendbar.

### 6.4 Rename / Move / Delete (Metadaten-only)

```
MOVE/rename: DB-Tx: parent_id/name ändern (+ Advisory-Lock auf Ziel-Parent)
             → kein S3-Zugriff. Konflikt → 409/412.
DELETE:      trashed_at setzen (Papierkorb); Worker räumt nach Frist endgültig
             → Blob-Refcount-- , unreferenzierte Blobs per GC entfernen.
```

---

## 7. Sicherheitsarchitektur

| Bereich | Maßnahme |
|---|---|
| Transport | TLS 1.2+ überall: HTTPS am Ingress, FTPS im FTP-Dienst (explicit AUTH TLS). Plain-FTP wird auf Protokollebene abgelehnt. |
| AuthN Web/API | OIDC/SSO oder lokale Session; API-Tokens mit Scopes; MFA für Adminrollen. |
| AuthN FTPS | FTP-Login gegen zentralen Credential-Store (Over TLS). Optional Client-Zertifikate. |
| AuthZ | Zentraler PDP, deny by default, serverseitig bei jeder Operation; identisch für HTTP und FTPS. |
| Pfad-Sicherheit | Ausschließlich virtueller Namespace; keine OS-Pfade, keine Symlinks nach außen. |
| Presigned URLs | Kurze TTL, objektgebunden, keine S3-Credentials im Client. |
| Upload-Sicherheit | Größen-/Typ-Limits, SHA-256-Pflicht, Virenscan vor Freigabe (Ausbaustufe). |
| FTP-Härtung | PORT-Bounce-Angriffe validieren/verbieten, Passive-Range begrenzen, `masquerade address` korrekt setzen. |
| Brute-Force | Lockout/Backoff, Session-Timeouts, Rate-Limits. |
| Secrets | Secret-Manager/Umgebungsvariablen, Rotation, keine Secrets in Logs. |
| Ruhende Daten | S3-SSE (SSE-S3/SSE-KMS) + verschlüsselte DB-Volumes. |
| Audit | Append-only-Log aller Auth- und Mutationsereignisse inkl. Protokoll und IP. |

---

## 8. Schnittstellen/API-Skizze (REST)

```
Auth      POST   /api/auth/login            POST /api/auth/token
          GET    /api/me

Namespace GET    /api/entries?parent={id}   GET  /api/entries/{id}
          POST   /api/folders               PATCH /api/entries/{id}
          DELETE /api/entries/{id}          POST /api/entries/{id}/move
          POST   /api/entries/{id}/copy

Transfer  GET    /api/entries/{id}/content   (Range → 206, HEAD analog)
          POST   /api/uploads                (Init, tus-kompatibel)
          PATCH  /api/uploads/{id}           POST /api/uploads/{id}/complete
          DELETE /api/uploads/{id}

Sharing   POST   /api/shares                GET  /api/shares/{token}
          DELETE /api/shares/{token}

Suche     GET    /api/search?q=&type=&parent=

Admin     /api/users  /api/groups  /api/quotas  /api/audit

System    GET    /healthz   GET /metrics   GET /api/version
```

**Konventionen:** ETag-basierte optimistische Nebenläufigkeit (`If-Match` → `412`),
`Idempotency-Key` für Upload-Init, einheitliche Fehlerobjekte (`problem+json`),
Paginierung über Cursor.

**FTPS-Kommandoprofil:** `AUTH TLS`/`PBSZ`/`PROT P`, `USER`/`PASS`, `PWD`/`CWD`/`CDUP`,
`LIST`/`NLST`/`MLSD`, `SIZE`/`MDTM`, `RETR`/`STOR`/`APPE`, `REST`, `DELE`,
`RNFR`/`RNTO`, `MKD`/`RMD`, `PASV`/`EPSV`, `FEAT`, `OPTS UTF8 ON`.

---

## 9. Technologie-Mapping (Empfehlung)

| Schicht | Umsetzung | Begründung |
|---|---|---|
| Core + HTTP-API + FTP-Gateway | **Python 3.11+ / FastAPI** + **pyftpdlib** + **boto3** | Gesetzt: ein Sprach-Stack, schnelle Entwicklung, ausgereifte FTP-/S3-Libs. |
| Web-UI | **React + TypeScript** (MVP: minimales statisches UI) | Klare API-Trennung; Ausbau mit Dateimanager-Komponenten |
| Metadaten-DB | **PostgreSQL 16** | ACL/Vererbung, FTS, Transaktionen, bewährt |
| Objektspeicher | **S3-kompatibel** (MinIO on-prem / AWS / Ceph / Wasabi) | Gesetzt (E2), kein Lock-in durch S3-API |
| Resumable Upload | **tus-ähnliches Protokoll** (eigene Implementierung) | Standardnah, kein Extra-Dienst im MVP |
| Suche | Postgres-FTS (MVP) → OpenSearch (später) | Kein Extra-Dienst im MVP |
| Virenscan | ClamAV-Sidecar (später) | Asynchron vor Freigabe |
| Ingress | Traefik oder Caddy | Automatisches TLS, einfache Config |
| Deployment | **Docker Compose** (on-prem), Kubernetes später | Gesetzt: Betriebsarmut passend zur Teamgröße |
| Observability | OpenTelemetry + Prometheus + Grafana/Loki | Standard, offen |

**Entscheidung zum Nebenläufigkeitsmodell:** Der **Core ist synchron** (SQLAlchemy 2.0 sync +
boto3). Die FastAPI-Endpunkte laufen als synchrone Handler im Threadpool, das FTPS-Gateway
(pyftpdlib ist synchron) nutzt **denselben** Core unverändert. Das vermeidet zwei parallele
Core-Implementierungen (async vs. sync) und hält die garantierte Rechte-Gleichheit (P1) einfach.
Async lohnt hier erst bei sehr hoher Nebenläufigkeit und wäre ein späterer, isolierter Umbau.

**Verworfene Alternativen:** Go (bessere Streaming-Effizienz, aber zweiter Sprach-Stack neben
einem TS-Frontend) und Node/NestJS (ein Sprach-Stack, aber schwächeres Ökosystem für FTPS).

---

## 10. Deployment und Betrieb

```
Rollen aus einem Image:
  api     – REST + Auslieferung der SPA
  ftp     – FTPS auf 21/TCP + Passive-Range 30000–30100/TCP  (direkt exponiert!)
  worker  – Virenscan, Suche-Index, Trash-GC, Blob-GC, Outbox-Versand

Infrastruktur:
  PostgreSQL (Persistenz)  ·  S3/MinIO (Blobs)  ·  Ingress (nur HTTPS)
```

- **Konfiguration** vollständig per Umgebungsvariablen / IaC, Secrets getrennt.
- **Backup:** PostgreSQL PITR (WAL) + S3-Bucket-Versionierung; optional Cross-Region-Replik.
- **Health:** `/healthz` (liveness/readiness), `/metrics` (Prometheus).
- **Skalierung:** zunächst vertikal; später `api`/`ftp` horizontal, da Core zustandslos
  (Zustand liegt in Postgres/S3). Migration zu Kubernetes bleibt offen.

---

## 11. Querschnittsthemen

| Thema | Festlegung |
|---|---|
| Nebenläufigkeit | Optimistische Sperren über ETag/`seq`, `412 Precondition Failed`; Advisory-Locks für Rename im Ziel-Parent. Kein harter File-Lock (FTP/HTTP kennen ihn nicht). |
| Idempotenz | `Idempotency-Key` beim Upload-Init; wiederholte Completion ist gefahrlos. |
| Integrität | SHA-256 beim Upload berechnet/geprüft; Blob = CAS, damit selbsterklärend prüfbar. |
| Konsistenz | DB ist führend; S3 nur Blob-Speicher. Kein Verlass auf S3-Listing. |
| Events | Transaktionale Outbox → Worker (Suche, Scan, Benachrichtigung). |
| Observability | Strukturierte JSON-Logs, Metriken, OTel-Traces; Korrelations-ID pro Request/Transfer. |
| Fehlerbehandlung | Einheitliches Fehlerformat, keine internen Details nach außen. |

---

## 12. Architekturentscheidungen (ADR-Kurzform)

| ADR | Entscheidung | Begründung / Alternative |
|---|---|---|
| ADR-1 | **Modularer Monolith mit Rollen** statt Microservices | Kleinste Betriebskomplexität, gemeinsamer Kern garantiert P1. Alternative: getrennte Services – Overhead ohne Nutzen bei Teamgröße. |
| ADR-2 | **Namespace in Postgres, Blobs content-addressed in S3** | Löst Rename/Ordner/Atomicität; S3 bleibt reiner Objektspeicher. |
| ADR-3 | **Ein Binary, Rollen `api`/`ftp`/`worker`** | Identischer Core, einfache Skalierung später. |
| ADR-4 | **Streaming durch Core als Default**, Presigned-URLs opt-in | Volle Kontrolle/Audit; Direkttransfer nur bei sehr großen Dateien. |
| ADR-5 | **Optimistische Nebenläufigkeit (ETag)**, keine File-Locks | Passt zu HTTP und FTP; Konflikte explizit. WebDAV-LOCK später separat. |
| ADR-6 | **`APPE` = Anhängen über Multipart/Objekt-Komposition**, ggf. mit klarer Fehlermeldung, wenn inaktiv | S3-Objekte sind unveränderlich; echtes Append ist teuer. |
| ADR-7 | **FTPS direkt exponiert**, kein L7-Proxy; feste Passive-Range | FTP ist kein HTTP; Proxies terminieren FTPS nicht. |
| ADR-8 | **Soft-Delete/Papierkorb + asynchroner GC** | Wiederherstellung und sichere Blob-Bereinigung. |
| ADR-9 | **CAS-Dedup aktiv** | Speicherersparnis; Kosten: Refcount/GC. Bei Bedarf abschaltbar. |

---

## 13. Offene Punkte vor der Umsetzung

Bereits entschieden: Stack **Python/FastAPI**, Betrieb **Docker Compose on-prem**,
Verschlüsselung ruhender Daten **S3-SSE + verschlüsselte DB-Volumes**.

Noch zu klären:

1. **S3-Provider** konkret wählen (MinIO on-prem, AWS, Ceph, Wasabi).
2. **Presigned-URLs** aktivieren oder ausschließlich Core-Streaming?
3. **Dedup (CAS)** an/aus und maximale Dateigröße/Passive-Portrange festlegen.
4. **RPO/RTO** und Backup-Ziel konkretisieren.
5. **Altgeräte ohne TLS**: WebDAV, Upload-Formular oder Verzicht?
6. **Konflikt-UI/-Policy** bei parallelen Schreibzugriffen über verschiedene Protokolle.
