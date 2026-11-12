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

> **Rollen → Berechtigungen:** Diese Umstellung führt **keine neuen
> Konfigurationsvariablen** ein. Die Grants liegen in der Datenbank
> (`acl`, `system_permissions`); Migrationen siehe [Betrieb](betrieb.md#71-rollen--berechtigungen-reihenfolge).

> **Ausnahme:** `IFS_S3_UI_USER` und `IFS_S3_UI_PASSWORD` werden nur vom Deploy-Skript
> `deploy/setup-seaweedfs.sh` für die SeaweedFS-UI verwendet; die Anwendung selbst
> wertet sie nicht aus.

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
| `IFS_AUTO_CREATE_SCHEMA` | bool | `false` | Legt beim Start Tabellen per `create_all` an. **Nur Dev/Erststart**, in Produktion `false` und Migrationen nutzen (siehe [Betrieb](betrieb.md)). |

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
| `IFS_ACCESS_TOKEN_TTL_SECONDS` | int | `3600` | Gültigkeitsdauer eines Tokens bzw. der Cookie-Session in Sekunden. |
| `IFS_AUTH_COOKIE_NAME` | string | `ifs_session` | Name des `HttpOnly`-Session-Cookies der Web-UI. In Produktion empfiehlt sich ein `__Host-`-Präfix (z. B. `__Host-ifs_session`); das erzwingt `Secure`, `Path=/` und verbietet ein `Domain`-Attribut. Leerzeichen, `;`, `=` und `,` sind ungültig. |
| `IFS_BEARER_ALLOWED_NETWORKS` | string | `127.0.0.1/32,::1/128` | Kommagetrennte CIDR-Liste, aus der Bearer-Tokens akzeptiert werden. Leer = kein IP-basierter Bearer-Zugriff. Unabhängig davon darf jedes Konto mit dem Systemrecht `api` Bearer-Tokens nutzen. Die Web-UI benötigt diese Freigabe nicht (Cookie-Session). |
| `IFS_LOGIN_MAX_ATTEMPTS` | int | `5` | Fehlversuche pro Konto (IP+Benutzer) bis zur Sperre. |
| `IFS_LOGIN_IP_MAX_ATTEMPTS` | int | `20` | Fehlversuche pro IP (Schutz vor Credential-Spraying). |
| `IFS_LOGIN_WINDOW_SECONDS` | int | `300` | Zeitfenster, in dem Fehlversuche gezählt werden. |
| `IFS_LOGIN_LOCK_SECONDS` | int | `30` | Basisdauer der Sperre (verdoppelt sich je weiterem Fehlversuch). |
| `IFS_LOGIN_MAX_LOCK_SECONDS` | int | `900` | Maximale Sperrdauer. |

> Ein Wechsel von `IFS_JWT_SECRET` invalidiert alle ausgestellten Tokens und
> Cookie-Sessions sofort. Die Web-UI nutzt ausschließlich ein `HttpOnly`-Cookie
> (`SameSite=Strict`); Bearer-Tokens sind für API-Clients gedacht und werden nur
> aus `IFS_BEARER_ALLOWED_NETWORKS` oder für Konten mit Systemrecht `api`
> akzeptiert. Beispiel für ein Administrationsnetz: `10.13.13.0/24`.

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
| `IFS_S3_MAX_POOL_CONNECTIONS` | int | `32` | Maximale Zahl gleichzeitiger Verbindungen im S3-Connection-Pool (botocore-Standard: `10`). Ein größerer Pool verhindert, dass parallele FTPS-/HTTP-Transfers sich serialisieren. |
| `IFS_S3_MULTIPART_THRESHOLD_MB` | int | `8` | Ab dieser Größe wird ein Upload als S3-Multipart-Transfer durchgeführt. |
| `IFS_S3_MULTIPART_CHUNKSIZE_MB` | int | `16` | Part-Größe bei Multipart-Uploads. Größere Parts erhöhen den Durchsatz, belegen aber mehr RAM (Faustregel: `chunksize × concurrency`). |
| `IFS_S3_TRANSFER_CONCURRENCY` | int | `8` | Parallele Parts je Multipart-Upload. Mehr Parallelität = mehr Durchsatz, aber mehr Speicher/CPU. |

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

```bash
# HTTP-Transfers (Produktionsbeispiel: LAN, 1-GBit-Clients)
IFS_HTTP_STREAM_CHUNK_BYTES=4194304
IFS_HTTP_DOWNLOAD_PARALLELISM=1
IFS_HTTP_DOWNLOAD_TOKEN_TTL_SECONDS=3600
IFS_HTTP_UPLOAD_PART_BYTES=16777216
IFS_HTTP_UPLOAD_PARALLELISM=4
```

## HTTP-Transfers

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_HTTP_STREAM_CHUNK_BYTES` | int | `4194304` | Blockgröße beim Streamen aus S3 Richtung Client in Bytes. Untergrenze **65536** (64 KiB). Größere Blöcke senken Read-/ASGI-/Socket-Overhead; die Parallelität begrenzt den Speicherbedarf. |
| `IFS_HTTP_DOWNLOAD_PARALLELISM` | int | `1` | Zahl paralleler S3-Range-Requests pro Download. `1` = klassischer einzelner S3-Stream (Default, auf dem Testsystem gemessen schneller als 4). Werte werden auf **1–16** begrenzt. Speicherbedarf pro Download ≈ `IFS_HTTP_STREAM_CHUNK_BYTES × IFS_HTTP_DOWNLOAD_PARALLELISM`. |
| `IFS_HTTP_DOWNLOAD_TOKEN_TTL_SECONDS` | int | `3600` | Gültigkeit des kurzlebigen Download-Tickets (Untergrenze 30 s). Lang genug für Range-Resume großer Dateien; das Ticket ist an Benutzer, Eintrag und `token_version` gebunden. |
| `IFS_HTTP_UPLOAD_PART_BYTES` | int | `16777216` | Part-Größe für parallele HTTP-Uploads in Bytes. Wird auf **5 MiB–64 MiB** geklemmt (S3-Multipart-Untergrenze). Die Web-UI verwendet diesen Wert. |
| `IFS_HTTP_UPLOAD_PARALLELISM` | int | `4` | Anzahl gleichzeitig übertragener Parts; der Server meldet sie der Web-UI. Werte werden auf **1–8** begrenzt. |

> **Speicher:** Beim Download werden höchstens `Parallelität` Fenster à
> `STREAM_CHUNK_BYTES` gleichzeitig gehalten (Default 1 × 4 MiB = 4 MiB, bei
> Parallelität 4 = 16 MiB pro aktivem Download). Beim Upload schreibt jeder Part
> in eine eigene Spool-Datei; der zusätzliche Plattenplatz entspricht der
> Dateigröße, bis `complete` aufräumt. Details und Messwerte:
> [HTTP-Transfers](http-transfers.md).

### Low-Memory-Profil (2 vCPU / ca. 2 GB RAM)

Für kleine Hosts sind diese Werte ein guter Ausgangspunkt. Sie senken den
Peak-RAM pro Transfer und die CPU-Konkurrenz, kosten aber etwas Durchsatz:

| Variable | Wert | Wirkung |
|---|---|---|
| `IFS_S3_MULTIPART_CHUNKSIZE_MB` | `8` | 8 MiB × 4 = 32 MiB statt 128 MiB Peak |
| `IFS_S3_TRANSFER_CONCURRENCY` | `4` | weniger parallele S3-Parts/Threads |
| `IFS_S3_MAX_POOL_CONNECTIONS` | `8` | weniger Sockets/Puffer pro Rolle |
| `IFS_HTTP_STREAM_CHUNK_BYTES` | `2097152` | 2 MiB pro aktivem Download |
| `IFS_HTTP_UPLOAD_PART_BYTES` | `8388608` | 2 × 8 MiB Browser-Puffer |
| `IFS_HTTP_UPLOAD_PARALLELISM` | `2` | weniger parallele Parts |
| `IFS_FTP_TRANSFER_BUFFER_BYTES` | `131072` | 128 KiB pro FTP-Datenkanal |
| `IFS_FTP_S3_READAHEAD_BYTES` | `2097152` | 2 MiB Read-Ahead |
| `IFS_FTP_FINALIZE_WORKERS` | `1` | ein Finalisierungs-Thread |
| `IFS_WORKER_INTERVAL_SECONDS` | `30` | weniger Leerlauf-Scans |
| `IFS_WORKER_OUTBOX_BATCH_SIZE` | `50` | kleinere Batches |

Auf dem Container-Host zusätzlich `GOMEMLIMIT=768MiB` für SeaweedFS setzen
(siehe `docker-compose.yml`) und `-s3.concurrentUploadLimitMB=128`,
`-volume.concurrentUploadLimitMB=128`, `-volume.readBufferSizeMB=1`,
`-volume.compactionMBps=50` und `-volume.maintenanceMBps=20`.
**`-s3.readerCacheSizeMB` auf `0` (Default/unbeschränkt) lassen:** Ein Limit von
64 MiB hat auf dem Testsystem den Lesedurchsatz von ~150 MB/s auf ~10 MB/s
einbrechen lassen; die RAM-Begrenzung erfolgt über `GOMEMLIMIT`. Das ist reine
Konfiguration; `leveldb`-Index und größere Volumes sind bewusst **nicht** Teil
dieses Profils.

## Uploads

| Variable | Typ | Standard | Beschreibung |
|---|---|---|---|
| `IFS_UPLOAD_SPOOL_DIR` | string | `/var/lib/ifs/spool` | Verzeichnis für temporäre Upload-Daten (resumable Sessions). Muss beschreibbar und ausreichend groß sein. |
| `IFS_MAX_UPLOAD_SIZE` | int | `0` | Maximale Dateigröße in Bytes. `0` = unbegrenzt. Bei Überschreitung antwortet die API mit `413` – **vor** dem Schreiben (Content-Length/angekündigte Größe). Beim FTP-Upload bricht der Transfer ab, sobald das Limit überschritten wird. |
| `IFS_SHARE_UPLOAD_MAX_SIZE` | int | `104857600` | Hartes Größenlimit für **öffentliche Share-Uploads** in Bytes (100 MiB). Greift zusätzlich zu `IFS_MAX_UPLOAD_SIZE`; `0` = unbegrenzt. |
| `IFS_SHARE_UPLOAD_MAX_REQUESTS` | int | `60` | Maximale öffentliche Share-Uploads pro Freigabe+IP je Zeitfenster. |
| `IFS_SHARE_UPLOAD_WINDOW_SECONDS` | int | `300` | Zeitfenster für `IFS_SHARE_UPLOAD_MAX_REQUESTS`. |
| `IFS_PUBLISHED_DOWNLOAD_MAX_REQUESTS` | int | `120` | Maximale anonyme Downloads einer Veröffentlichung pro Eintrag+IP je Zeitfenster. |
| `IFS_PUBLISHED_DOWNLOAD_WINDOW_SECONDS` | int | `300` | Zeitfenster für `IFS_PUBLISHED_DOWNLOAD_MAX_REQUESTS`. |
| `IFS_DEFAULT_QUOTA_BYTES` | int | `0` | Gesamt-Quota pro Besitzer in Bytes. `0` = unbegrenzt. Wird bei jedem Schreiben geprüft (`413` bei Überschreitung). |

> Der Spool ist eine **Zwischenablage**: Resumable Uploads sammeln Chunks dort und
> werden erst bei Abschluss nach S3 übertragen. Der Platzbedarf entspricht der größten
> gleichzeitig laufenden Datei.

## Netzwerk und Informations-Endpunkte

> **Hinweis hinter einem Reverse-Proxy:** Ohne gesetztes `IFS_TRUSTED_PROXIES` sehen alle
> Clients wie ein einziger aus (die Proxy-IP). Login-Sperre und Rate-Limits wirken dann
> global statt pro Client – die Proxy-Netze (z. B. `172.16.0.0/12`) deshalb eintragen.
>
> **nginx und Uploads:** nginx begrenzt Request-Bodies standardmäßig auf **1 MB**
> (`client_max_body_size`) und antwortet bei größeren Uploads mit `413`, bevor die API
> prüft. Für Uploads mindestens die Part-Größe der Web-UI (`IFS_HTTP_UPLOAD_PART_BYTES`,
> Standard 16 MiB) oder `0` (unbegrenzt) setzen; siehe
> [Produktiv-Deployment](../tutorials/09-produktiv-deployment.md#alternative-https-über-nginx).

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
| `IFS_FTP_TRANSFER_BUFFER_BYTES` | int | `262144` | Blockgröße des FTPS-Datenkanals (Socket-Puffer und Datei-Producer) in Bytes. Untergrenze 16 KiB. Größere Blöcke senken Syscall- und TLS-Overhead, kosten pro aktivem Transfer etwas RAM. |
| `IFS_FTP_S3_READAHEAD_BYTES` | int | `4194304` | S3-Read-Ahead je Download in Bytes (Untergrenze 64 KiB). Innerhalb eines Bereichs wird das Objekt mit **einem** S3-Request gestreamt statt mit einem Request je 64-KiB-Block. |
| `IFS_FTP_FINALIZE_WORKERS` | int | `2` | Parallele Worker für die Finalisierung kleiner FTP-Uploads (S3/DB) nach dem `226`. `0` = synchron. Vor lesenden Namespace-Kommandos (`LIST`, `RETR`, `SIZE`, …) wartet der Server auf offene Finalisierungen. |

> **Durchsatz:** Die drei Werte `IFS_FTP_TRANSFER_BUFFER_BYTES`,
> `IFS_FTP_S3_READAHEAD_BYTES` und `IFS_S3_MAX_POOL_CONNECTIONS` sollten zur
> Umgebung passen. Bei WAN-Anbindung sind große Read-Ahead-Bereiche und ein
> großer Connection-Pool besonders wirksam. Messwerte und Zusammenhänge:
> [FTPS-Handbuch → Durchsatz und Tuning](ftps.md#7-durchsatz-und-tuning).

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
IFS_S3_MAX_POOL_CONNECTIONS=32

IFS_UPLOAD_SPOOL_DIR=/var/lib/ifs/spool
IFS_MAX_UPLOAD_SIZE=0

IFS_FTP_ENABLED=true
IFS_FTP_HOST=0.0.0.0
IFS_FTP_PORT=21
IFS_FTP_PASSIVE_PORTS=30000-30099
IFS_FTP_MASQUERADE_ADDRESS=files.example.org
IFS_FTP_CERTFILE=/etc/ifs/ftps.crt
IFS_FTP_KEYFILE=/etc/ifs/ftps.key
IFS_FTP_TRANSFER_BUFFER_BYTES=262144
IFS_FTP_S3_READAHEAD_BYTES=4194304

IFS_ADMIN_USERNAME=admin
IFS_ADMIN_PASSWORD=<starkes-passwort>
```

---

## Einstellungen prüfen

- **Laufzeit:** `GET /version` liefert Name und Version (bewusst **ohne** Umgebungsangabe); `GET /healthz` nur `{"status":"ok"}`.
- **Start:** Bei ungültigen Werten bricht der Prozess mit einer Pydantic-Fehlermeldung ab.
- **Container:** `docker compose config` validiert die zusammengeführte Compose-Konfiguration.

Weiter: [Betrieb](betrieb.md) · [Sicherheit](sicherheit.md) · [Troubleshooting](../tutorials/10-troubleshooting.md)
