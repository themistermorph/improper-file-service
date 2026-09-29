# Tutorial 6 – Freigabelinks

**Ziel:** Eine Datei per öffentlichem Link teilen – mit Passwort, Ablaufdatum und
Downloadlimit – und den Link wieder widerrufen.

**Voraussetzungen:** laufendes IFS; ein angemeldeter Benutzer mit `share`-Recht am
Eintrag (der Besitzer hat es implizit).

---

## 1. Datei vorbereiten

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

echo "Vertraulicher Inhalt" > geheim.txt
ENTRY=$(curl -s -X PUT "$HOST/api/uploads/simple?name=geheim.txt" \
  -H "$AUTH" --data-binary @geheim.txt \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "entry=$ENTRY"
```

---

## 2. Freigabe erstellen

```bash
SHARE=$(curl -s -X POST $HOST/api/shares -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_id\":\"$ENTRY\",\"password\":\"geheim-123\",\"max_downloads\":3, \
       \"expires_at\":\"2026-12-31T23:59:59Z\"}")
echo "$SHARE"
TOKEN_SHARE=$(echo "$SHARE" | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
```

Parameter:

| Feld | Wirkung |
|---|---|
| `password` | Zugriff nur mit Passwort |
| `expires_at` | Ablaufzeitpunkt (UTC) |
| `max_downloads` | maximale Anzahl Downloads |
| `allow_upload` | Upload in einen geteilten **Ordner** über den Link erlauben |

---

## 3. Link weitergeben und testen

Der Link hat die Form:

```
$HOST/api/shares/<token>/content/<dateiname>
```

Für einen Ordner ist `<dateiname>` der `<ordnername>.zip` – so speichert auch
`curl -O` (ohne Zusatzoption) direkt als ZIP. Ohne Dateinamen im Pfad funktioniert der
Download weiterhin über `$HOST/api/shares/<token>/content`.

Test **ohne** Passwort → `401`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$HOST/api/shares/$TOKEN_SHARE/content"
```

Download **mit** Passwort:

```bash
curl -s -H "X-Share-Password: geheim-123" \
  "$HOST/api/shares/$TOKEN_SHARE/content" -o geheim.txt
cat geheim.txt
```

**Wichtig:** Für den öffentlichen Download ist **kein** Bearer-Token nötig.

### Ordner teilen

Wird statt einer Datei ein **Ordner** freigegeben, liefert derselbe Link den kompletten
Teilbaum als **ZIP-Archiv** aus (`Content-Type: application/zip`, Dateiname
`<Ordnername>.zip`). Der Download zählt wie ein normaler Download (Limit, Passwort und
Ablauf gelten unverändert).

```bash
FOLDER=$(curl -s -X POST $HOST/api/folders -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"team"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -s -X POST $HOST/api/shares -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_id\":\"$FOLDER\"}" | python -m json.tool

curl -s "$HOST/api/shares/$TOKEN_SHARE/content/team.zip" -o team.zip
unzip -l team.zip
```

> **`curl -O`:** Da der Dateiname im Pfad steht, speichert
> `curl -O "$HOST/api/shares/$TOKEN_SHARE/content/team.zip"` automatisch als
> `team.zip`. (Der Header `Content-Disposition` nennt den Namen zusätzlich, was mit
> `curl -OJ` auch über `/content` funktioniert.)

### Upload über den Link (Drop)

Bei einem **Ordner** kann die Freigabe Uploads erlauben. Dazu `allow_upload: true`
setzen und den Link wie folgt nutzen – der Body ist der Dateiinhalt
(`allow_upload` erfordert `write` am Ordner):

```bash
FOLDER_SHARE=$(curl -s -X POST $HOST/api/shares -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_id\":\"$FOLDER\",\"allow_upload\":true}" | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -T bericht.pdf "$HOST/api/shares/$FOLDER_SHARE/upload?name=bericht.pdf"
```

Bei einem Namenskonflikt wird automatisch `bericht (2).pdf` angelegt. Sollen Uploads
vorhandene Dateien ersetzen, muss das bei der Freigabe erlaubt werden
(`overwrite: true`, ebenfalls nur mit `write` am Ordner); ein
`?overwrite=true` am Upload-Request allein wirkt nicht mehr.

---

## 4. Metadaten abrufen

```bash
curl -s "$HOST/api/shares/$TOKEN_SHARE" | python -m json.tool
```

```json
{
  "token": "...",
  "entry_id": "...",
  "expires_at": "2026-12-31T23:59:59Z",
  "max_downloads": 3,
  "downloads": 1,
  "has_password": true,
  "allow_upload": false,
  "overwrite": false
}
```

---

## 5. Downloadlimit testen

```bash
# Drei weitere Abrufe (Limit = 3 insgesamt)
for i in 1 2 3; do
  curl -s -o /dev/null -w "Download $i: %{http_code}\n" \
    -H "X-Share-Password: geheim-123" "$HOST/api/shares/$TOKEN_SHARE/content"
done
```

Ab Erreichen des Limits antwortet der Dienst mit **410 Gone**.

---

## 6. Freigabe widerrufen

```bash
curl -s -X DELETE "$HOST/api/shares/$TOKEN_SHARE" -H "$AUTH" -o /dev/null -w "%{http_code}\n"
# danach: öffentlicher Zugriff -> 404
```

---

## 7. Sicherheitshinweise

- Links sind **zufällige Tokens** und nicht erratbar, aber „Bearer“-artig: wer den Link
  kennt, kommt an die Datei (ohne Passwortschutz).
- Nutze **Ablauf** und **Downloadlimit** für sensible Daten.
- Alle Freigabe-Downloads werden im **Audit-Log** als `share.download` erfasst.
- Nach Ablauf/Limit liefert der Dienst **410 Gone** (nicht 404), um den Unterschied
  zwischen „unbekannt“ und „nicht mehr gültig“ sichtbar zu machen.

Weiter mit [Tutorial 7 – Backup & Restore](07-backup-restore.md).
