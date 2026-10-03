# Tutorial 3 – FTPS mit FileZilla und curl

**Ziel:** Dateien per FTPS hoch- und herunterladen, einen unterbrochenen Transfer
fortsetzen und die Ergebnisse über die API prüfen.

**Voraussetzungen:** laufendes IFS mit gültigem FTPS-Zertifikat
(siehe [Installation](../docs/installation.md)); ein FTPS-fähiger Client.

---

## Schritt 1 – Vorbereitung

Stelle sicher, dass der FTP-Dienst läuft und die Passive-Ports erreichbar sind:

```bash
docker compose ps ftp
docker compose logs ftp | tail -n 5
```

Erzeuge einen Testordner und einen Benutzer (über die API):

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Ordner "projekte"
PROJECTS_ID=$(curl -s -X POST $HOST/api/folders -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"projekte"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "projekte=$PROJECTS_ID"
```

Für dieses Tutorial verwenden wir den Admin-Account. Ein eigener Benutzer folgt in
[Tutorial 5](05-benutzer-gruppen-rechte.md).

---

## Schritt 2 – Verbindung mit FileZilla

1. **Datei → Servermanager → Neuer Server**
2. Einstellungen:
   - Protokoll: **FTP – File Transfer Protocol**
   - Host: `localhost` (bzw. Server-Host)
   - Port: `21`
   - Verschlüsselung: **Require explicit FTP over TLS**
   - Anmeldetyp: **Normal**
   - Benutzer: `admin`, Passwort
3. **Verbinden**. Beim selbstsignierten Dev-Zertifikat die Warnung bestätigen.

Du siehst die IFS-Ordnerstruktur (Wurzel, `projekte`, …).

---

## Schritt 3 – Upload und Download per FileZilla

- **Upload:** Datei aus dem lokalen Bereich ins rechte (Server-)Fenster ziehen.
- **Download:** Datei auf die linke Seite ziehen.
- **Umbenennen:** Rechtsklick → *Umbenennen* (Metadaten-Operation).
- **Löschen:** Rechtsklick → *Löschen* (landet im Papierkorb).

---

## Schritt 4 – Verbindung mit curl

```bash
FTPHOST=localhost
USER=admin
PASS=$IFS_ADMIN_PASSWORD

# Listing
curl --ssl-reqd -u "$USER:$PASS" "ftp://$FTPHOST/projekte/"

# Upload
echo "per FTPS hochgeladen" > ftps-test.txt
curl --ssl-reqd -u "$USER:$PASS" -T ftps-test.txt \
  "ftp://$FTPHOST/projekte/ftps-test.txt"

# Download
curl --ssl-reqd -u "$USER:$PASS" \
  "ftp://$FTPHOST/projekte/ftps-test.txt" -o zurueck.txt
cat zurueck.txt
```

---

## Schritt 5 – Unterbrochenen Transfer fortsetzen

**Download fortsetzen** (curl):

```bash
# Erste 5 Bytes laden
curl --ssl-reqd -u "$USER:$PASS" -r 0-4 \
  "ftp://$FTPHOST/projekte/ftps-test.txt" -o partial.txt

# Rest automatisch anhängen
curl --ssl-reqd -u "$USER:$PASS" -C - \
  "ftp://$FTPHOST/projekte/ftps-test.txt" -o partial.txt
cat partial.txt
```

**Upload fortsetzen** (lftp): `put -c` setzt den Transfer fort.

---

## Schritt 6 – Verbindung mit Python (`ftplib`)

```python
import ftplib, os, ssl

ctx = ssl.create_default_context()
# Nur für Dev mit selbstsigniertem Zertifikat:
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ftp = ftplib.FTP_TLS(context=ctx)
try:
    ftp.connect("localhost", 21, timeout=10)
    ftp.login("admin", os.environ["IFS_ADMIN_PASSWORD"])
    ftp.prot_p()

    print("PWD:", ftp.pwd())
    print("Listing:", ftp.nlst("/projekte"))

    with open("ftps-test.txt", "wb") as fh:
        ftp.retrbinary("RETR /projekte/ftps-test.txt", fh.write)

    with open("neu.txt", "rb") as fh:
        ftp.storbinary("STOR /projekte/neu.txt", fh)
finally:
    ftp.quit()
```

---

## Schritt 7 – Ergebnis über die API prüfen

```bash
# Wurzel öffnen, projekte-ID ermitteln
curl -s -H "Authorization: Bearer $TOKEN" \
  "$HOST/api/entries?parent_id=$PROJECTS_ID"
```

Die per FTPS übertragenen Dateien erscheinen mit denselben Metadaten (Größe, SHA-256,
Zeitstempel) wie bei einem HTTP-Upload – es ist derselbe Bestand.

---

## Hinweise

- **Nur FTPS:** Klartext-FTP wird abgelehnt; Clients müssen TLS können.
- **Passive-Ports:** Hinter Firewall/NAT `IFS_FTP_PASSIVE_PORTS` öffnen und ggf.
  `IFS_FTP_MASQUERADE_ADDRESS` setzen.
- `STOU` (Store Unique) wird nicht unterstützt.
- Rechte gelten identisch zu HTTP.

Weiter mit [Tutorial 4 – Resumable Uploads](04-resumable-uploads.md).
