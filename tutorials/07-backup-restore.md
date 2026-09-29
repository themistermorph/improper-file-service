# Tutorial 7 – Backup & Restore

**Ziel:** Metadaten und Blobs konsistent sichern und nach einem „Unfall“ vollständig
wiederherstellen.

**Voraussetzungen:** IFS läuft mit Docker Compose; der Objektspeicher ist erreichbar
(standardmäßig lokales SeaweedFS mit Volume `s3-data`, alternativ externes S3). Für einen
S3-Export ein CLI (AWS CLI, rclone oder `mc`) verwenden.

> Lokal kann alternativ das Volume `s3-data` direkt gesichert werden (Volume-Snapshot).

---

## 1. Was gesichert werden muss

| Bestand | Inhalt | Werkzeug |
|---|---|---|
| PostgreSQL | Namespace, Versionen, Rechte, Audit | `pg_dump` / `pg_restore` |
| S3-Bucket | alle Blobs (Dateiinhalte) | S3-CLI (`aws s3 sync` / rclone) |

Beide Bestände müssen aus **demselben Stand** stammen.

---

## 2. Testdaten erzeugen

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

mkdir -p backups

curl -s -X POST $HOST/api/folders -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"backup-test"}' >/dev/null

FOLDER=$(curl -s -H "Authorization: Bearer $TOKEN" "$HOST/api/entries" \
  | python -c "import sys,json;print([e['id'] for e in json.load(sys.stdin) if e['name']=='backup-test'][0])")

echo "wichtige Daten" > wichtige-datei.txt
ENTRY=$(curl -s -X PUT "$HOST/api/uploads/simple?parent_id=$FOLDER&name=wichtige-datei.txt" \
  -H "Authorization: Bearer $TOKEN" --data-binary @wichtige-datei.txt \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "entry=$ENTRY"
```

---

## 3. Backup erstellen

```bash
STAMP=$(date +%Y-%m-%d_%H%M%S)

# 1) Metadaten
docker compose exec -T db pg_dump -U ifs -Fc ifs > "backups/ifs-$STAMP.dump"
ls -lh "backups/ifs-$STAMP.dump"

# 2) Blobs (externes S3)
aws s3 sync "s3://$IFS_S3_BUCKET" "backups/blobs-$STAMP"
# oder: rclone sync s3:$IFS_S3_BUCKET "backups/blobs-$STAMP"
```

Empfehlung für Produktion: zusätzlich **S3-Bucket-Versionierung** und **PostgreSQL
PITR** aktivieren.

---

## 4. „Unfall“ simulieren

```bash
# Datei in den Papierkorb (Soft-Delete) und zusätzlich Blob/DB-Eintrag hart entfernen
curl -s -X DELETE "$HOST/api/entries/$ENTRY" -H "Authorization: Bearer $TOKEN" -o /dev/null -w "DELETE: %{http_code}\n"

# Optional: Ordner endgültig aus der DB entfernen (rauer simulieren)
docker compose exec -T db psql -U ifs -d ifs -c \
  "DELETE FROM entries WHERE name = 'backup-test';"

# Bucket leeren (nur für die Übung!)
aws s3 rm "s3://$IFS_S3_BUCKET" --recursive >/dev/null
```

Prüfe, dass die Daten weg sind:

```bash
aws s3 ls --recursive "s3://$IFS_S3_BUCKET" | head
```

---

## 5. Wiederherstellen

```bash
# 1) Metadaten zurückspielen
docker compose exec -T db pg_restore -U ifs -d ifs --clean --if-exists \
  < "backups/ifs-$STAMP.dump"

# 2) Blobs zurückspielen
aws s3 sync "backups/blobs-$STAMP" "s3://$IFS_S3_BUCKET"

# 3) Dienste neu starten
docker compose restart api ftp worker
```

---

## 6. Ergebnis prüfen

```bash
# Metadaten wieder da?
curl -s -H "Authorization: Bearer $TOKEN" "$HOST/api/entries?parent_id=$FOLDER"

# Inhalt wieder da?
curl -s -H "Authorization: Bearer $TOKEN" "$HOST/api/entries/$ENTRY/content"
```

Erwartung: `wichtige Daten`.

---

## 7. Automatisierung (Cron)

```bash
cat > /etc/cron.d/ifs-backup <<'EOF'
# Täglich 02:15 – Metadaten + Blobs inkl. 7-Tage-Rotation
15 2 * * * root /opt/ifs/scripts/backup.sh >> /var/log/ifs-backup.log 2>&1
EOF
```

`/opt/ifs/scripts/backup.sh` (Auszug):

```bash
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date +%Y-%m-%d)
cd /opt/ifs
docker compose exec -T db pg_dump -U ifs -Fc ifs > "backups/ifs-$STAMP.dump"
aws s3 sync "s3://$IFS_S3_BUCKET" "backups/blobs-$STAMP"
find backups -name 'ifs-*.dump' -mtime +7 -delete
find backups -maxdepth 1 -name 'blobs-*' -type d -mtime +7 -exec rm -rf {} +
```

---

## 8. RPO/RTO

| Kennzahl | Bedeutung | Einfluss |
|---|---|---|
| **RPO** | maximal akzeptabler Datenverlust | Häufigkeit von Backup/PITR |
| **RTO** | maximale Wiederherstellungszeit | Größe der Bestände, Übungsgrad |

Beide Zielwerte müssen mit dem Betreiber festgelegt werden. Regelmäßige
**Restore-Proben** sind Pflicht – ein ungetestetes Backup ist kein Backup.

Weiter mit [Tutorial 8 – API-Anbindung](08-api-anbindung-python.md).
