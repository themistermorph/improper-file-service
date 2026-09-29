# Tutorial 10 – Troubleshooting

**Ziel:** Typische Probleme schnell eingrenzen und beheben.

---

## 1. Erste Schritte bei jedem Problem

```bash
docker compose ps                 # laufen alle Dienste?
docker compose logs api   | tail -n 50
docker compose logs ftp   | tail -n 50
docker compose logs worker| tail -n 50

curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/version
```

Im Log steht meist die konkrete Ursache (Konfiguration, DB-/S3-Verbindung).

---

## 2. HTTP/API

| Symptom | Ursache | Lösung |
|---|---|---|
| `401 Unauthorized` | fehlender/abgelaufener Token, falsches Passwort | neu einloggen; Header `Authorization: Bearer …` prüfen |
| `403 Forbidden` | fehlendes Recht | ACL/Besitz prüfen; [Tutorial 5](05-benutzer-gruppen-rechte.md) |
| `404 Not Found` | Eintrag gelöscht/nie existiert | Pfad/ID prüfen; Soft-Delete nach 30 Tagen endgültig |
| `409 Conflict` | Name existiert oder Upload-Offset falsch | anderen Namen wählen; Offset per `HEAD` lesen |
| `413 Payload Too Large` | `IFS_MAX_UPLOAD_SIZE` überschritten | Limit erhöhen oder Datei verkleinern |
| `416 Range Not Satisfiable` | Range außerhalb der Datei | Größe per `HEAD` ermitteln, `Content-Range` beachten |
| `500 Blob fehlt` | Metadaten vorhanden, Blob in S3 fehlt | Restore aus Backup; S3-Zugriff/Bucket prüfen |
| `503`/Verbindungsfehler | api down oder DB nicht erreichbar | `docker compose ps`, DB-Logs prüfen |

### Token manuell prüfen

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/me
```

### Audit-Log als Diagnosehilfe

```bash
curl -s -H "Authorization: Bearer $TOKEN" "localhost:8000/api/audit?limit=20" | python -m json.tool
```

---

## 3. Datenbank

| Symptom | Ursache | Lösung |
|---|---|---|
| Startfehler „connection refused“ | DB nicht bereit | `depends_on`/Healthcheck abwarten, `docker compose logs db` |
| „relation does not exist“ | Schema fehlt | `IFS_AUTO_CREATE_SCHEMA=true` (Dev) oder `alembic upgrade head` |
| Migration schlägt fehl | Schema veraltet/inkonsistent | Backup, dann Migration prüfen |

```bash
# Verbindung testen
docker compose exec db pg_isready -U ifs -d ifs
docker compose exec -T db psql -U ifs -d ifs -c '\dt'
```

---

## 4. Objektspeicher (S3)

IFS nutzt standardmäßig **lokales SeaweedFS** (persistentes Volume `s3-data`), alternativ
einen externen S3-kompatiblen Speicher. Den Zustand prüft das Preflight-Skript bzw. die
Storage-UI unter http://localhost:9010 (Basic Auth).

| Symptom | Ursache | Lösung |
|---|---|---|
| Bucket konnte nicht geprüft werden | Endpoint/Zugang falsch oder Speicher nicht erreichbar | `bash deploy/preflight-s3.sh`; `IFS_S3_ENDPOINT_URL`/Region prüfen |
| `SignatureDoesNotMatch` | falsche Keys | `IFS_S3_ACCESS_KEY`/`SECRET_KEY` prüfen |
| Verbindung zu AWS schlägt fehl | Region/SSL | `IFS_S3_REGION`, `IFS_S3_USE_SSL` prüfen |
| Uploads hängen | Netzwerk/Bucket-Rechte | Erreichbarkeit, Bucket-Policy/IAM prüfen |
| `500 Blob fehlt` bei alten Dateien | Blob im neuen Speicher nicht vorhanden | `python -m ifs.cli cleanup-missing-blobs` |

```bash
# Preflight (Endpoint, Range, Multipart)
bash deploy/preflight-s3.sh

# Bucket-Inhalt (Beispiel AWS CLI)
aws s3 ls "s3://$IFS_S3_BUCKET"
```
```

---

## 5. FTPS

| Symptom | Ursache | Lösung |
|---|---|---|
| Verbindung sofort geschlossen | Client ohne TLS | explizites FTPS aktivieren |
| „AUTH TLS“ fehlgeschlagen | Zertifikat fehlt/ungültig | `IFS_FTP_CERTFILE`/`KEYFILE` prüfen |
| Login schlägt fehl | Benutzer/Passwort falsch oder inaktiv | Benutzer in API prüfen |
| Datenkanal hängt nach `LIST` | Passive-Ports blockiert | Firewall/NAT öffnen, `IFS_FTP_PASSIVE_PORTS` prüfen |
| `PASV` liefert interne IP | NAT ohne Masquerade | `IFS_FTP_MASQUERADE_ADDRESS` setzen |
| `550` bei `STOU` | nicht unterstützt | normalen `STOR` verwenden |
| Rechteprobleme | kein `read`/`write`/`delete` | Rechte prüfen; identisch zu HTTP |

### Verbindung isoliert testen

```bash
# curl mit TLS
curl -v --ssl-reqd -u admin:admin ftp://localhost/ 2>&1 | head -n 30

# lftp mit Debug
lftp -d -u admin,admin ftps://localhost
```

- Im `lftp`-Prompt `set ftp:passive-mode on`.
- Bei Zertifikatswarnungen in Dev-Clients die Prüfung deaktivieren.

---

## 6. Worker / Hintergrundjobs

| Symptom | Ursache | Lösung |
|---|---|---|
| Papierkorb wird nicht geleert | Worker läuft nicht | `docker compose ps worker`, neu starten |
| Spool-Verzeichnis läuft voll | abgebrochene Uploads | Worker räumt nach 24 h; Volume vergrößern |
| Verwaiste Blobs bleiben | Worker-Fehler | `docker compose logs worker` |

---

## 7. Uploads

| Symptom | Ursache | Lösung |
|---|---|---|
| `409` beim `PATCH` | Offset stimmt nicht | `HEAD /api/uploads/{id}` → `Upload-Offset` verwenden |
| `400` bei `complete` | Größe/SHA-256 falsch | erwartete Werte korrigieren |
| Upload bricht ab | Spool voll oder Verbindung | Spool-Platz prüfen; Chunks kleiner wählen, fortsetzen |
| `404` bei `PATCH` | Session abgelaufen (24 h) | neue Session anlegen |

---

## 8. Diagnose-Spickzettel

```bash
# Läuft alles?
docker compose ps

# Rolle neu starten
docker compose restart api        # bzw. ftp / worker

# Konfiguration prüfen
docker compose config

# In den Container
docker compose exec api sh

# DB-Tabellen
docker compose exec db psql -U ifs -d ifs -c '\dt'

# Blobs
mc ls --recursive local/ifs | head

# Health/Metriken
curl -s localhost:8000/healthz
curl -s localhost:8000/metrics | head
```

---

## 9. Wenn nichts hilft

Sammle für eine Fehlermeldung:

1. `docker compose ps`
2. relevante Logs (letzte 50 Zeilen je Rolle)
3. `curl -s localhost:8000/version`
4. die fehlgeschlagene Anfrage inkl. Statuscode und Antworttext
5. die letzten Audit-Einträge

Damit lässt sich das Problem meist schnell eingrenzen.

Zurück zum [Dokumentationsindex](../docs/README.md).
