# Konfiguration

IFS wird vollständig über Umgebungsvariablen konfiguriert. Alle Variablen tragen das
Präfix **`IFS_`**. Zusätzlich liest die Anwendung beim Start eine `.env`-Datei im
Arbeitsverzeichnis (siehe `.env.example`).

**Regeln**

- Umgebungsvariablen haben Vorrang vor `.env`.
- Änderungen erfordern einen **Neustart** des jeweiligen Dienstes (API, FTP, Worker).
- Die Konfiguration wird beim ersten Zugriff gecacht (`get_settings()`); Tests leeren
  diesen Cache.
- Werte sind typisiert (Pydantic); ein ungültiger Wert verhindert den Start mit klarer
  Fehlermeldung.

---

## Allgemein

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_APP_NAME` | string | `IFS` | Anzeigename (z. B. in `/version`) |
| `IFS_ENVIRONMENT` | string | `dev` | Umgebungsbezeichnung (`dev`, `staging`, `prod`) |
| `IFS_LOG_LEVEL` | string | `INFO` | Log-Level (`DEBUG`, `INFO`, `WARNING`, …) |

## Datenbank

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_DATABASE_URL` | string | `postgresql+psycopg://ifs:ifs@localhost:5432/ifs` | SQLAlchemy-URL. PostgreSQL im Betrieb, SQLite nur für Tests. |
| `IFS_DB_PASSWORD` | string | `ifs` | Nur für `docker-compose`: Passwort für den `db`-Dienst und die zusammengesetzte `IFS_DATABASE_URL`. In Produktion ändern. |
| `IFS_AUTO_CREATE_SCHEMA` | bool | `false` | Legt beim Start Tabellen per `create_all` an. **Nur Dev/Erststart**, in Produktion `false` und Alembic nutzen. |

> In `IFS_ENVIRONMENT=prod` verweigert der Start, wenn `IFS_DATABASE_URL` die
> Default-Zugangsdaten (`ifs:ifs`, `postgres:postgres`, `root:root`, `admin:admin`) enthält.

Beispiel PostgreSQL:

```dotenv
IFS_DB_PASSWORD=ein-langes-zufaelliges-passwort
IFS_DATABASE_URL=postgresql+psycopg://ifs:geheim@db:5432/ifs
```

## Authentifizierung

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_JWT_SECRET` | string | *(leer)* | HMAC-Schlüssel (≥ 32 Zeichen). In `prod` ist ein starker Wert **Pflicht**, sonst startet der Dienst nicht; in Dev wird sonst ein zufälliges Secret erzeugt. |
| `IFS_JWT_ALGORITHM` | string | `HS256` | JWT-Algorithmus; erlaubt sind `HS256`, `HS384`, `HS512`. In Produktion werden andere Werte abgelehnt. |
| `IFS_ACCESS_TOKEN_TTL_SECONDS` | int | `3600` | Gültigkeitsdauer eines Tokens in Sekunden. |
| `IFS_LOGIN_MAX_ATTEMPTS` | int | `5` | Fehlversuche pro Konto (IP+Benutzer) bis zur Sperre. |
| `IFS_LOGIN_IP_MAX_ATTEMPTS` | int | `20` | Fehlversuche pro IP (Schutz vor Credential-Spraying). |
| `IFS_LOGIN_WINDOW_SECONDS` | int | `300` | Zeitfenster, in dem Fehlversuche gezählt werden. |
| `IFS_LOGIN_LOCK_SECONDS` | int | `30` | Basisdauer der Sperre (verdoppelt sich je weiterem Fehlversuch). |
| `IFS_LOGIN_MAX_LOCK_SECONDS` | int | `900` | Maximale Sperrdauer. |

> Ein Wechsel von `IFS_JWT_SECRET` invalidiert alle ausgestellten Tokens sofort.

## Objektspeicher (S3)

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_S3_ENDPOINT_URL` | string \| leer | *(leer)* | S3-Endpunkt. Leer = AWS S3. Für MinIO/Ceph die URL setzen. |
| `IFS_S3_ACCESS_KEY` | string | `minioadmin` | Access Key des S3-Kontos (`setup-seaweedfs.sh` setzt einen eigenen; `.env.example` lässt ihn leer). |
| `IFS_S3_SECRET_KEY` | string | `minioadmin` | Secret Key des S3-Kontos (`setup-seaweedfs.sh` setzt einen eigenen). |
| `IFS_S3_BUCKET` | string | `ifs` | Bucket für Blobs. |
| `IFS_S3_REGION` | string | `us-east-1` | Region. |
| `IFS_S3_USE_SSL` | bool | `false` | TLS zum S3-Endpunkt. |
| `IFS_S3_SSE` | string \| leer | `AES256` | Serverseitige Verschlüsselung: `AES256` (SSE-S3), `aws:kms` (SSE-KMS) oder leer (aus). |
| `IFS_S3_PRESIGN_TTL_SECONDS` | int | `300` | Gültigkeit präsignierter URLs (aktuell nur als Option vorgesehen). |
| `IFS_CAS_DEDUP` | bool | `true` | Content-addressed Dedup: identische Inhalte werden nur einmal gespeichert. |

**Provider-Beispiele**

```dotenv
# Lokales SeaweedFS (Standard-Compose)
IFS_S3_ENDPOINT_URL=http://s3:8333
IFS_S3_USE_SSL=false
IFS_S3_SSE=

# AWS S3 (Endpoint leer lassen)
IFS_S3_ENDPOINT_URL=
IFS_S3_REGION=eu-central-1
IFS_S3_USE_SSL=true
IFS_S3_SSE=AES256

# Ceph RGW (on-prem)
IFS_S3_ENDPOINT_URL=https://rgw.example.org
IFS_S3_USE_SSL=true
IFS_S3_SSE=

# MinIO (on-prem)
IFS_S3_ENDPOINT_URL=https://minio.example.org
IFS_S3_USE_SSL=true
IFS_S3_SSE=

# Ceph RGW / Wasabi (S3-kompatibel)
IFS_S3_ENDPOINT_URL=https://s3.eu-central-1.wasabisys.com
IFS_S3_USE_SSL=true
```

## Uploads

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_UPLOAD_SPOOL_DIR` | string | `/var/lib/ifs/spool` | Verzeichnis für temporäre Upload-Daten (resumable Sessions). Muss beschreibbar und ausreichend groß sein. |
| `IFS_MAX_UPLOAD_SIZE` | int | `0` | Maximale Dateigröße in Bytes. `0` = unbegrenzt. Bei Überschreitung antwortet die API mit `413` – **vor** dem Schreiben (Content-Length/angekündigte Größe). Beim FTP-Upload bricht der Transfer ab, sobald das Limit überschritten wird. |
| `IFS_SHARE_UPLOAD_MAX_SIZE` | int | `104857600` | Hartes Größenlimit für **öffentliche Share-Uploads** in Bytes (100 MiB). Greift zusätzlich zu `IFS_MAX_UPLOAD_SIZE`; `0` = unbegrenzt. |
| `IFS_SHARE_UPLOAD_MAX_REQUESTS` | int | `60` | Maximale öffentliche Share-Uploads pro Freigabe+IP je Zeitfenster. |
| `IFS_SHARE_UPLOAD_WINDOW_SECONDS` | int | `300` | Zeitfenster für `IFS_SHARE_UPLOAD_MAX_REQUESTS`. |
| `IFS_DEFAULT_QUOTA_BYTES` | int | `0` | Gesamt-Quota pro Besitzer in Bytes. `0` = unbegrenzt. Wird bei jedem Schreiben geprüft (`413` bei Überschreitung). |

> Der Spool ist eine **Zwischenablage**: Resumable Uploads sammeln Chunks dort und
> werden erst bei Abschluss nach S3 übertragen. Der Platzbedarf entspricht der größten
> gleichzeitig laufenden Datei.

## Netzwerk und Informations-Endpunkte

> **Hinweis hinter einem Reverse-Proxy:** Ohne gesetztes `IFS_TRUSTED_PROXIES` sehen alle
> Clients wie ein einziger aus (die Proxy-IP). Login-Sperre und Rate-Limits wirken dann
> global statt pro Client – die Proxy-Netze (z. B. `172.16.0.0/12`) deshalb eintragen.

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_TRUSTED_PROXIES` | string | *(leer)* | Kommagetrennte CIDRs vertrauenswürdiger Proxies. Nur dann wird `X-Forwarded-For` für die Audit-IP ausgewertet – sonst gilt die Peer-IP. |
| `IFS_EXPOSE_DOCS` | bool | `false` | Stellt `/docs`, `/redoc`, `/openapi.json` bereit. Standardmäßig deaktiviert. |
| `IFS_METRICS_PUBLIC` | bool | `false` | Macht `/metrics` ohne Auth erreichbar. Standard: nur für Admins. |
| `IFS_MONITOR_DISK_PATH` | string | `/` | Pfad, dessen Belegung im Admin-Panel „System“ angezeigt wird. |
| `IFS_SEAWEEDFS_STATUS_URL` | string | *(leer)* | SeaweedFS-Volume-Status für das Admin-Panel, z. B. `http://s3:9333/vol/status`. Leer = ausgeblendet. |

## Papierkorb

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_TRASH_RETENTION_DAYS` | int | `30` | Aufbewahrungsfrist in Tagen; danach entfernt der Worker gelöschte Einträge endgültig. |

## Worker

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_WORKER_INTERVAL_SECONDS` | int | `10` | Poll-Intervall des Workers (Outbox, Papierkorb, Blob-GC) in Sekunden. |
| `IFS_WORKER_OUTBOX_BATCH_SIZE` | int | `200` | Maximale Anzahl Outbox-Events, die der Worker pro Durchlauf verarbeitet. |

## FTPS

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_FTP_ENABLED` | bool | `true` | FTPS-Dienst aktiv. Für reine HTTP-Installationen abschaltbar. |
| `IFS_FTP_HOST` | string | `0.0.0.0` | Bind-Adresse. |
| `IFS_FTP_PORT` | int | `21` | Control-Port. |
| `IFS_FTP_PASSIVE_PORTS` | string | `30000-30100` | Passive-Portrange (`start-end` oder Einzelport). Muss durch Firewall/NAT offen sein. |
| `IFS_FTP_MASQUERADE_ADDRESS` | string \| leer | *(leer)* | Nach außen sichtbare Adresse/Name. **Im Docker-/NAT-Betrieb zwingend** – sonst meldet `PASV` die Container-IP und der Datenkanal läuft ins Timeout. |
| `IFS_FTP_CERTFILE` | string | *(leer)* | Pfad zum TLS-Zertifikat (PEM). **Pflicht**, sobald FTPS aktiv ist. |
| `IFS_FTP_KEYFILE` | string | *(leer)* | Pfad zum privaten Schlüssel (PEM). |
| `IFS_FTP_BANNER` | string | `IFS FTPS ready` | Begrüßungstext beim Verbinden. |

> Plain-FTP (ohne TLS) wird **nicht** unterstützt. Clients müssen explizites FTPS
> (`AUTH TLS`) beherrschen. Details im [FTPS-Handbuch](ftps.md).

## Admin-Erstinitialisierung

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_ADMIN_USERNAME` | string | `admin` | Benutzername des initialen Administrators. |
| `IFS_ADMIN_PASSWORD` | string \| leer | *(leer)* | Passwort des initialen Administrators. **Nur wenn gesetzt**, wird beim ersten Start ein Admin angelegt (mind. 8 Zeichen). Ohne Wert wird kein Default-Admin erstellt. |

Der Seed greift nur, wenn **noch kein** Benutzer existiert. Er ist damit idempotent.

---

## Referenzbeispiel (Produktion)

```dotenv
IFS_ENVIRONMENT=prod
IFS_LOG_LEVEL=INFO

IFS_DATABASE_URL=postgresql+psycopg://ifs:geheim@db:5432/ifs
IFS_AUTO_CREATE_SCHEMA=false

IFS_JWT_SECRET=mit-einem-passwortmanager-erzeugter-32+-byte-schluessel
IFS_ACCESS_TOKEN_TTL_SECONDS=3600

IFS_S3_ENDPOINT_URL=
IFS_S3_REGION=eu-central-1
IFS_S3_USE_SSL=true
IFS_S3_BUCKET=ifs-prod
IFS_S3_SSE=aws:kms
IFS_CAS_DEDUP=true

IFS_UPLOAD_SPOOL_DIR=/var/lib/ifs/spool
IFS_MAX_UPLOAD_SIZE=0

IFS_FTP_ENABLED=true
IFS_FTP_HOST=0.0.0.0
IFS_FTP_PORT=21
IFS_FTP_PASSIVE_PORTS=30000-30099
IFS_FTP_MASQUERADE_ADDRESS=files.example.org
IFS_FTP_CERTFILE=/etc/ifs/ftps.crt
IFS_FTP_KEYFILE=/etc/ifs/ftps.key

IFS_ADMIN_USERNAME=admin
IFS_ADMIN_PASSWORD=<starkes-passwort>
```

---

## Einstellungen prüfen

- **Laufzeit:** `GET /version` liefert Name und Version (bewusst **ohne** Umgebungsangabe); `GET /healthz` nur `{"status":"ok"}`.
- **Start:** Bei ungültigen Werten bricht der Prozess mit einer Pydantic-Fehlermeldung ab.
- **Container:** `docker compose config` validiert die zusammengeführte Compose-Konfiguration.

Weiter: [Betrieb](betrieb.md) · [Sicherheit](sicherheit.md) · [Troubleshooting](../tutorials/10-troubleshooting.md)
