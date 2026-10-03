# Betrieb

Dieses Dokument richtet sich an Administratorinnen und Administratoren, die IFS
betreiben: Start/Stop, Monitoring, Backup, Upgrades, Skalierung und Notfallmaßnahmen.

---

## 1. Rollen und Topologie

IFS wird als **ein Image mit drei Rollen** betrieben:

| Rolle | Start | Aufgabe | Zustand |
|---|---|---|---|
| `api` | `python -m ifs.cli run-api` | REST-API + Web-UI | zustandslos |
| `ftp` | `python -m ifs.cli run-ftp` | FTPS-Gateway | zustandslos |
| `worker` | `python -m ifs.cli run-worker` | Outbox, Papierkorb, Blob-GC | zustandslos |

Dazu die Infrastruktur:

```
Clients ──HTTPS──► Ingress (TLS) ──► api ─┐
Clients ──FTPS───► ftp (direkt, TCP)  ────┼──► PostgreSQL   (Metadaten/Rechte)
                                          ├──► S3           (Blobs)
                             worker ◄─────┘
```

Alle Rollen sind zustandslos; der Zustand liegt in PostgreSQL und S3. Damit sind sie
einzeln neustart- und (später) horizontal skalierbar.

---

## 2. Starten, Stoppen, Neustarten

```bash
# Start / Stop
docker compose up -d
docker compose down

# Einzelne Rolle neu starten (z. B. nach Zertifikatstausch)
docker compose restart ftp
docker compose restart api

# Logs verfolgen
docker compose logs -f api
docker compose logs -f ftp
docker compose logs -f worker

# Zustand
docker compose ps
```

Nach Konfigurationsänderungen in `.env` ist ein Neustart der betroffenen Rolle nötig.

---

## 3. Health, Version, Metriken

| Endpunkt | Zweck |
|---|---|
| `GET /healthz` | Liveness/Readiness; liefert nur `{"status":"ok"}` |
| `GET /version` | Name, Version (bewusst ohne Umgebungsangabe) |
| `GET /metrics` | Prometheus-Textmetriken (`ifs_users`, `ifs_entries`, `ifs_stored_bytes`) |

Beispiel-Prometheus-Scrape:

```yaml
scrape_configs:
  - job_name: ifs
    metrics_path: /metrics
    static_configs:
      - targets: ["ifs-api:8000"]
```

Healthchecks sind für `db` **und** `s3` (SeaweedFS) konfiguriert; `api`/`ftp`/`worker`
hängen per `depends_on: condition: service_healthy` von beiden ab.

---

## 4. Logging

- Strukturierte Logs auf `stdout`/`stderr` (Log-Level über `IFS_LOG_LEVEL`).
- In Compose mit `docker compose logs` sichtbar; in Kubernetes/Systemd an die zentrale
  Log-Pipeline weiterleiten.
- Fachliche Ereignisse zusätzlich im **Audit-Log** (`GET /api/audit`).

---

## 5. Backup und Wiederherstellung

IFS besteht aus **zwei** Datenbeständen, die konsistent zueinander gesichert werden
müssen:

1. **PostgreSQL** – Namespace, Rechte, Metadaten, Audit.
2. **Objektspeicher** – alle Blobs (lokal SeaweedFS-Volume `s3-data` **oder** externer S3).

> Lokal: das Docker-Volume `s3-data` sichern (Volume-Snapshot/`docker run --rm -v ifs_s3-data:/d … tar`) **oder**
> S3-seitig exportieren. Extern: die Backup-/Versionierungsfunktionen des Anbieters nutzen.

### 5.1 Backup

```bash
# 1. Metadaten (logischer Dump)
docker compose exec db pg_dump -U ifs -Fc ifs > ifs-$(date +%F).dump

# 2. Blobs spiegeln (Beispiel: S3-CLI gegen den konfigurierten Endpoint)
aws --endpoint-url "$IFS_S3_ENDPOINT_URL" s3 sync "s3://$IFS_S3_BUCKET" ./ifs-blobs-backup
# oder: rclone sync s3:$IFS_S3_BUCKET ./ifs-blobs-backup
```

Empfehlung: **S3-Bucket-Versionierung** und **PostgreSQL PITR** (WAL-Archivierung)
aktivieren, um auch Zeitpunkte zwischen den Sicherungen abzudecken.

### 5.2 Wiederherstellung

```bash
# 1. Metadaten zurückspielen (in eine leere DB)
docker compose exec -T db pg_restore -U ifs -d ifs --clean < ifs-YYYY-MM-DD.dump

# 2. Blobs zurückspielen
mc mirror --overwrite ./ifs-blobs-backup local/ifs

# 3. Dienste neu starten
docker compose restart api ftp worker
```

> Reihenfolge ist unkritisch, solange beide Bestände aus demselben Sicherungsstand
> stammen. Ein Metadaten-Dump ohne die zugehörigen Blobs führt zu Einträgen mit
> fehlendem Inhalt.

### 5.3 Konsistenz prüfen

Der Worker räumt verwaiste Blobs automatisch auf. Nach einem Restore lohnt ein Blick in
die Worker-Logs; fehlende Blobs erkennt man an `500 Blob fehlt`-Antworten beim Download.

Details und ein durchgerechnetes Beispiel: [Tutorial 7](../tutorials/07-backup-restore.md).

---

## 6. Aufbewahrung und Garbage Collection

Der Worker läuft standardmäßig alle 10 Sekunden und erledigt:

| Aufgabe | Wirkung |
|---|---|
| Outbox abarbeiten | markiert Events als veröffentlicht |
| Abgelaufene Uploads abbrechen | räumt Spool-Dateien nach 24 h |
| Papierkorb leeren | endgültiges Löschen nach 30 Tagen |
| Blob-GC | entfernt unreferenzierte Blobs aus DB und S3 |

Die Upload-Frist ist als `UPLOAD_SESSION_TTL_HOURS` in `src/ifs/worker.py` hinterlegt;
die Papierkorb-Frist steuert `IFS_TRASH_RETENTION_DAYS` (Standard 30 Tage).

---

## 7. Upgrades und Migrationen

```bash
# 1. Backup erstellen (siehe oben)
# 2. Neuen Stand einspielen
git pull
docker compose build

# 3. SQL-Migrationen anwenden (migrations/sql/, in Reihenfolge)
for f in migrations/sql/000*.sql; do
  docker compose exec -T db psql -U ifs -d ifs < "$f"
done
#    Vor 0006_per_user_roots.sql muss für jeden Benutzer eine Wurzel existieren.
#    Enthält u. a. 0007_performance_indexes.sql und 0008_published_entries.sql.

# 4. Alembic nur für eigene Schema-Änderungen: Das mitgelieferte Docker-Image
#    enthält alembic.ini/migrations nicht; Standard-Upgrades laufen über Schritt 3.

# 5. Rollen neu starten
docker compose up -d
```

- `IFS_AUTO_CREATE_SCHEMA` in Produktion **niemals** aktiv lassen.
- Schemaänderungen immer als Alembic-Revision versionieren.

---

## 8. Skalierung

| Engpass | Maßnahme |
|---|---|
| Viele gleichzeitige HTTP-Transfers | `api`-Instanzen hinter dem Ingress vervielfachen |
| Viele FTPS-Sitzungen | `ftp` replizieren; Passive-Range je Instanz oder via L4-LB |
| Geringer FTPS-Durchsatz bei großen Dateien | `IFS_FTP_S3_READAHEAD_BYTES` und `IFS_FTP_TRANSFER_BUFFER_BYTES` prüfen (siehe [FTPS-Handbuch](ftps.md#7-durchsatz-und-tuning)) |
| Parallele Transfers serialisieren sich | `IFS_S3_MAX_POOL_CONNECTIONS` erhöhen |
| Viele Hintergrundjobs | `worker` replizieren (Outbox ist idempotent ausgelegt) |
| Speicher | S3-Backend skaliert eigenständig |
| Metadaten | PostgreSQL vertikal skalieren, Read-Replicas für Auswertungen |

Da alle Rollen zustandslos sind, genügt für horizontale Skalierung im Wesentlichen die
Erhöhung der Replikate. Der Übergang zu Kubernetes ist vorgesehen, aber nicht
Voraussetzung.

---

## 9. Runbooks

### Zertifikat wechseln (FTPS)

1. Neue `ftps.crt`/`ftps.key` bereitstellen.
2. `docker compose restart ftp`.
3. Verbindungsaufbau testen (`lftp`/`curl`).

### JWT-Secret rotieren

1. `IFS_JWT_SECRET` ändern.
2. `api` neu starten – alle bestehenden Tokens werden ungültig (Clients neu anmelden).

### Admin-Passwort zurücksetzen

```bash
# Benutzer in der DB anlegen/zurücksetzen via API (mit Admin-Token) …
# oder für den Erstadmin: Passwort-Hash über ein kleines Skript erzeugen.
```

### Notfall: API antwortet nicht

1. `docker compose ps` – laufen `db`, `s3`, `api`, `ftp`, `worker`?
2. `docker compose logs api` – Startfehler (Konfiguration, DB-Verbindung)?
3. `GET /healthz` prüfen.
4. Bei DB-Problemen: Backup einspielen (Abschnitt 5).

### Notfall: FTPS-Verbindung hängt im Datenkanal

- Passive-Ports offen? `IFS_FTP_PASSIVE_PORTS` und Firewall/NAT prüfen.
- Hinter NAT: `IFS_FTP_MASQUERADE_ADDRESS` gesetzt?
- Client auf passiven Modus stellen.

Weiter: [Troubleshooting](../tutorials/10-troubleshooting.md)
