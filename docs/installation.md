# Installation

Dieses Dokument beschreibt die Installation und Inbetriebnahme von IFS – sowohl
containerisiert mit **Docker Compose** (empfohlen) als auch **manuell** ohne Docker.

---

## 1. Systemvoraussetzungen

### Variante A – Docker Compose (empfohlen)

| Komponente | Anforderung |
|---|---|
| Docker Engine | ≥ 24 |
| Docker Compose | ≥ 2.20 (Plugin `docker compose`) |
| CPU / RAM | 2 vCPU / 2 GB RAM für den Start; mehr für Parallelbetrieb |
| Speicher | Hängt von Datenmenge ab; Postgres + Objektspeicher-Volume |
| Netzwerk | Ports 8000 (HTTP), 21 + Passive-Range (FTPS), 8333 (S3-API, nur intern) und 9010 (Storage-UI) |

### Variante B – Manuell (Python)

| Komponente | Anforderung |
|---|---|
| Python | ≥ 3.11 |
| PostgreSQL | ≥ 14 (getestet mit 16) |
| Objektspeicher | lokal: SeaweedFS (in der Compose) – oder extern: AWS S3, Ceph RGW, Wasabi |

---

## 2. Installation mit Docker Compose

### 2.1 Repository holen und Konfiguration anlegen

```bash
git clone <repository> ifs
cd ifs
cp .env.example .env
```

Öffne `.env` und ändere **mindestens**:

```dotenv
IFS_JWT_SECRET=<zufälliger String, 32+ Zeichen>
IFS_ADMIN_PASSWORD=<starkes Passwort>
```

> Für Produktion außerdem `IFS_AUTO_CREATE_SCHEMA` auf `false` setzen und Migrationen
> verwenden (siehe [Betrieb](betrieb.md)).

### 2.2 FTPS-Zertifikat erzeugen

FTPS benötigt ein Zertifikat. Für die Entwicklung genügt ein selbstsigniertes:

```bash
python scripts/gen-dev-cert.py certs
# erzeugt certs/ftps.crt und certs/ftps.key
```

Alternativ mit OpenSSL:

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/ftps.key -out certs/ftps.crt -days 365 \
  -subj "/CN=files.example.org"
```

Das Verzeichnis `./certs` wird von Compose nach `/etc/ifs` gemountet.

> **FTPS hinter NAT/Docker:** Setze in `.env` zusätzlich
> `IFS_FTP_MASQUERADE_ADDRESS` auf die von außen erreichbare Adresse (z. B.
> `files.example.org` oder die Server-IP). Ohne diese Angabe meldet der Server im
> `PASV`-Kommando seine Container-IP (etwa `172.18.0.x`); Clients wie WinSCP können den
> Datenkanal dann nicht öffnen („Datenverbindung zeitüberschritten“).

### 2.3 Starten

Setze zuerst in `.env` `IFS_JWT_SECRET` und `IFS_ADMIN_PASSWORD`, erzeuge die
Storage-Zugangsdaten und starte:

```bash
bash deploy/setup-seaweedfs.sh    # erzeugt S3-Zugangsdaten + Storage-UI-Login
docker compose up -d --build
bash deploy/preflight-s3.sh       # prüft Endpoint, Zugriff, Range & Multipart
docker compose ps
```

Die Compose betreibt **SeaweedFS** als lokalen S3-kompatiblen Speicher mit **persistentem
Volume** (`s3-data`). Für externes S3 (AWS/Ceph/Wasabi) stattdessen `IFS_S3_*` in `.env`
setzen und den `s3`-Service weglassen.

Beim ersten Start wird ein Admin-Benutzer angelegt, **sofern** `IFS_ADMIN_PASSWORD` gesetzt
ist; andernfalls siehe `python -m ifs.cli seed-admin --username admin --password …`.

### 2.4 Zugang prüfen

| Dienst | URL / Adresse |
|---|---|
| Web-UI | http://localhost:8000 |
| API-Dokumentation (Swagger) | http://localhost:8000/docs (nur mit `IFS_EXPOSE_DOCS=true`) |
| Health-Check | http://localhost:8000/healthz |
| Storage-UI (SeaweedFS, Basic Auth) | http://localhost:9010 |
| FTPS | `ftps://localhost:21` (explizites TLS) |

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}

curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}"
```

---

## 3. Manuelle Installation (ohne Docker)

### 3.1 Virtuelle Umgebung und Abhängigkeiten

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS
```

### 3.2 Externe Dienste bereitstellen

- **PostgreSQL** starten und Datenbank + Benutzer anlegen:

  ```sql
  CREATE USER ifs WITH PASSWORD 'ifs';
  CREATE DATABASE ifs OWNER ifs;
  ```

- **S3-Bucket** bereitstellen. Standard: lokales SeaweedFS (siehe `docker-compose.yml`);
  bei externem S3 den Bucket anlegen (oder vom Preflight anlegen lassen):

  ```bash
  # Beispiel mit AWS CLI gegen einen S3-kompatiblen Endpoint
  aws --endpoint-url "$IFS_S3_ENDPOINT_URL" s3 mb "s3://ifs"
  ```

### 3.3 Konfigurieren

```bash
export IFS_DATABASE_URL="postgresql+psycopg://ifs:ifs@localhost:5432/ifs"
# Lokales SeaweedFS:
export IFS_S3_ENDPOINT_URL="http://localhost:8333"
export IFS_S3_REGION="us-east-1"
export IFS_S3_ACCESS_KEY="<access-key>"
export IFS_S3_SECRET_KEY="<secret-key>"
export IFS_S3_BUCKET="ifs"
export IFS_S3_USE_SSL="false"
export IFS_S3_SSE=""
# Externes S3 stattdessen z. B.:
# export IFS_S3_ENDPOINT_URL=""   # leer = AWS
# export IFS_S3_REGION="eu-central-1"
# export IFS_S3_USE_SSL="true"
# export IFS_S3_SSE="AES256"
export IFS_JWT_SECRET="ein-sehr-langer-zufaelliger-string"
export IFS_UPLOAD_SPOOL_DIR="/var/lib/ifs/spool"
export IFS_FTP_CERTFILE="/etc/ifs/ftps.crt"
export IFS_FTP_KEYFILE="/etc/ifs/ftps.key"
# Nur wenn das Zertifikat nicht exakt auf die Client-Adresse lautet bzw. hinter NAT:
export IFS_FTP_MASQUERADE_ADDRESS="files.example.org"
```

(Komfortabler: `.env`-Datei im Arbeitsverzeichnis – wird automatisch gelesen.)

### 3.4 Initialisieren und starten

```bash
python -m ifs.cli init-db      # Schema anlegen + Bucket prüfen
python -m ifs.cli seed-admin   # Admin + dessen Home (jeder Benutzer erhält automatisch ein Home)
python -m ifs.cli run-api      # HTTP-API + Web-UI auf :8000
# zweites Terminal:
python -m ifs.cli run-ftp      # FTPS-Gateway
# drittes Terminal:
python -m ifs.cli run-worker   # Hintergrundjobs
```

---

## 4. Aktualisierung

> **Pflicht für bestehende Installationen (Per-User-Wurzel):** Die SQL-Migrationen
> unter `migrations/sql/` nacheinander anwenden – insbesondere
> `0004_unique_entry_names.sql`, `0005_share_overwrite.sql`,
> `0006_per_user_roots.sql`, `0007_performance_indexes.sql` und
> `0008_published_entries.sql` (öffentliche Galerie). **Vor** dem Unique-Index aus 0006
> muss für jeden Benutzer eine Wurzel existieren. Beispiel:
> ```bash
> for f in migrations/sql/000*.sql; do
>   docker compose exec -T db psql -U ifs -d ifs < "$f"
> done
> ```

### Docker Compose

```bash
git pull
docker compose build
docker compose up -d
```

### Manuell

```bash
git pull
.venv\Scripts\python -m pip install -e .
# Migrationen anwenden (SQL unter migrations/sql/, in Reihenfolge)
for f in migrations/sql/000*.sql; do psql "$IFS_DATABASE_URL" < "$f"; done
# Dienste neu starten
```

---

## 5. Deinstallation

```bash
docker compose down          # Container und Netzwerke entfernen
docker compose down -v       # zusätzlich Volumes (Daten!) löschen
```

> **Achtung:** `-v` entfernt PostgreSQL- und Objektspeicher-Volumes unwiderruflich.
> Vorher Backup erstellen (siehe [Tutorial 7](../tutorials/07-backup-restore.md)).

---

## 6. Nächste Schritte

- [Konfigurationsreferenz](konfiguration.md)
- [Tutorial 1 – Schnellstart](../tutorials/01-schnellstart.md)
- [Tutorial 9 – Produktiv-Deployment](../tutorials/09-produktiv-deployment.md)
