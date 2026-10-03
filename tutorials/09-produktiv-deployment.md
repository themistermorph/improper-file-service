# Tutorial 9 – Produktiv-Deployment

**Ziel:** IFS auf einem Server mit HTTPS, echtem Zertifikat, Firewall-Regeln, Backups und
Monitoring betreiben.

**Voraussetzungen:** Linux-Server, Domain (z. B. `files.example.org`), Docker + Compose,
ein S3-kompatibler Speicher oder AWS S3.

---

## 1. Zielarchitektur

```
                    Internet
                       │
         ┌─────────────┴─────────────┐
         │ 80/443  (HTTPS/Ingress)   │  Caddy/Traefik → api:8000
         │ 21 + 30000-30099 (FTPS)   │  direkt → ftp
         └─────────────┬─────────────┘
                       │
        api / ftp / worker  ──►  PostgreSQL (managed/Container)
                            ──►  S3 (managed/MinIO)
```

Merke: **FTPS geht nicht durch den HTTP-Proxy**. Nur HTTPS wird über den Ingress
veröffentlicht; FTPS wird per TCP exponiert.

---

## 2. Server vorbereiten

```bash
# Docker installieren (Beispiel Debian/Ubuntu)
curl -fsSL https://get.docker.com | sh

# Systembenutzer + Projektverzeichnis
sudo useradd -r -m -d /opt/ifs -s /usr/sbin/nologin ifs || true
sudo mkdir -p /opt/ifs && cd /opt/ifs
# Repository/Artefakt hierher deployen
```

---

## 3. Produktions-Konfiguration

`.env` (Auszug – vollständige Referenz in [Konfiguration](../docs/konfiguration.md)):

```dotenv
IFS_ENVIRONMENT=prod
IFS_LOG_LEVEL=INFO
IFS_DATABASE_URL=postgresql+psycopg://ifs:GEHEIM@db:5432/ifs
IFS_AUTO_CREATE_SCHEMA=false   # Achtung: die Standard-Compose überschreibt das (siehe Abschnitt 7)
IFS_JWT_SECRET=<- 48+ zufällige Bytes ->
IFS_S3_ENDPOINT_URL=                          # leer = AWS S3
IFS_S3_REGION=eu-central-1
IFS_S3_USE_SSL=true
IFS_S3_BUCKET=ifs-prod
IFS_S3_SSE=aws:kms
IFS_UPLOAD_SPOOL_DIR=/var/lib/ifs/spool
IFS_FTP_PASSIVE_PORTS=30000-30099
IFS_FTP_MASQUERADE_ADDRESS=files.example.org
IFS_FTP_CERTFILE=/etc/ifs/ftps.crt
IFS_FTP_KEYFILE=/etc/ifs/ftps.key
IFS_ADMIN_PASSWORD=<starkes Passwort>
```

Secrets nach Möglichkeit aus einem Secret-Manager beziehen statt aus `.env`.

---

## 4. HTTPS über Caddy

`docker-compose.override.yml`:

```yaml
services:
  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - api
    restart: unless-stopped

  api:
    # nicht mehr direkt veröffentlichen, sondern nur intern
    ports: []

volumes:
  caddy-data:
  caddy-config:
```

`Caddyfile`:

```
files.example.org {
    reverse_proxy api:8000
}
```

Caddy besorgt automatisch ein Let's-Encrypt-Zertifikat.

---

## 5. FTPS-Zertifikat

Nutze ein echtes Zertifikat mit dem Hostnamen der Domain (z. B. via certbot) und kopiere
es nach `./certs/ftps.crt` / `./certs/ftps.key`. Bei Erneuerung:

```bash
docker compose restart ftp
```

---

## 6. Firewall

```bash
# Nur die nötigen Ports öffnen
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 21/tcp
sudo ufw allow 30000:30099/tcp
sudo ufw enable
```

Der Passive-Bereich muss exakt dem `IFS_FTP_PASSIVE_PORTS` entsprechen.

---

## 7. Starten und Schema bereitstellen

> **Achtung, `IFS_AUTO_CREATE_SCHEMA`:** Der `api`-Dienst im mitgelieferten
> `docker-compose.yml` setzt `IFS_AUTO_CREATE_SCHEMA: "true"` **hart** im
> `environment`-Block. Compose-`environment` überschreibt `env_file` – der Wert
> `IFS_AUTO_CREATE_SCHEMA=false` aus der `.env` wirkt in dieser Standard-Compose also
> **nicht**. Für den Produktivbetrieb entweder den Eintrag in `docker-compose.yml` (bzw.
> einem Override) auf `"false"` setzen **und** die Migrationen selbst anwenden, oder
> bewusst bei `true` bleiben (Automatik) und die Migrationen für bestehende
> Installationen trotzdem anwenden.

**Nicht** über `alembic upgrade head` im Container migrieren: Das `Dockerfile` kopiert
nur `pyproject.toml`, `requirements.txt` und `src` – **nicht** `alembic.ini` bzw.
`migrations/`, und `migrations/versions/` ist leer. `docker compose run --rm api alembic
upgrade head` scheitert daher („No config file 'alembic.ini' found"). Nutze stattdessen
die **SQL-Migrationen** unter `migrations/sql/`:

```bash
docker compose build
docker compose up -d          # startet DB + API (Basisschema per create_all)
docker compose ps

# SQL-Migrationen in Reihenfolge anwenden (idempotent, inkl. 0008_published_entries.sql)
for f in migrations/sql/000*.sql; do
  docker compose exec -T db psql -U ifs -d ifs < "$f"
done
```

Die Migrationen sind idempotent (`IF NOT EXISTS`) und können wiederholt angewendet werden.
Für weitere Schemaänderungen siehe [Betrieb](../docs/betrieb.md#7-upgrades-und-migrationen).

Erster Login in der Web-UI → **Admin-Passwort ändern** (bzw. neuen Admin anlegen und den
Seed-Account deaktivieren).

---

## 8. Backups automatisieren

Richte den Cronjob aus [Tutorial 7](07-backup-restore.md) ein und teste die
Wiederherstellung **einmal bewusst**. Für Managed-PostgreSQL/S3 die jeweiligen
Snapshot-/PITR-Funktionen aktivieren.

---

## 9. Monitoring

- Prometheus-Scrape auf `/metrics` (siehe [Betrieb](../docs/betrieb.md)).
- Healthcheck `/healthz` in die Verfügbarkeitsüberwachung aufnehmen.
- Logs an eine zentrale Pipeline (Loki/ELK/CloudWatch) weiterleiten.
- Alarme: API nicht erreichbar, FTPS nicht erreichbar, DB-Verbindungsfehler,
  Worker-Lauf fehlgeschlagen, Spool-Verzeichnis voll.

---

## 10. Skalierung (optional)

Alle Rollen sind zustandslos:

```bash
docker compose up -d --scale api=3 --scale worker=2
```

Für FTPS bei mehreren Replikaten entweder je Replikat eine eigene Passive-Range
vergeben oder einen TCP-Load-Balancer einsetzen.

---

## 11. Go-Live-Checkliste

- [ ] `IFS_JWT_SECRET` und Admin-Passwort stark und geheim
- [ ] `IFS_AUTO_CREATE_SCHEMA` im `api`-Dienst explizit auf `false` gestellt (die
      Standard-Compose erzwingt sonst `"true"`), SQL-Migrationen
      `migrations/sql/000*.sql` ausgeführt
- [ ] HTTPS mit gültigem Zertifikat, HTTP→HTTPS-Redirect
- [ ] FTPS-Zertifikat gültig, Masquerade gesetzt, Passive-Range per Firewall offen
- [ ] Objektspeicher mit Verschlüsselung (`IFS_S3_SSE`)
- [ ] Backups laufen und Restore wurde getestet
- [ ] Monitoring/Alerting aktiv
- [ ] Audit-Log zugriffsgeschützt
- [ ] Sicherheits-Checkliste aus [Sicherheit](../docs/sicherheit.md) abgearbeitet

Weiter mit [Tutorial 10 – Troubleshooting](10-troubleshooting.md).
