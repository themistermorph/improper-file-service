# Tutorial 2 – Dateien über HTTP

**Ziel:** Den kompletten Datei-Lebenszyklus über die REST-API durchspielen: Ordner,
Upload, Listing, Download mit Resume, Umbenennen, Verschieben, Kopieren, Löschen.

**Voraussetzungen:** laufendes IFS; `curl`; optional `jq`.

---

## Schritt 0 – Anmelden

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "$TOKEN" | head -c 20
```

---

## Schritt 1 – Ordnerstruktur anlegen

```bash
# Ordner in der Wurzel
DOCS=$(curl -s -X POST $HOST/api/folders \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"docs"}' | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Unterordner
YEAR=$(curl -s -X POST $HOST/api/folders \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"parent_id\":\"$DOCS\",\"name\":\"2026\"}" \
  | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo "docs=$DOCS  year=$YEAR"
```

---

## Schritt 2 – Datei hochladen (einfach)

```bash
printf 'Zeile 1\nZeile 2\nZeile 3\n' > report.txt

ENTRY=$(curl -s -X PUT "$HOST/api/uploads/simple?parent_id=$YEAR&name=report.txt" \
  -H "Authorization: Bearer $TOKEN" \
  --data-binary @report.txt)
echo "$ENTRY"
```

Antwort (gekürzt):

```json
{"id":"0192...","name":"report.txt","size":22,"sha256":"..."}
```

Merke dir die `id` als `FILE_ID`.

---

## Schritt 3 – Auflisten

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$HOST/api/entries?parent_id=$YEAR"
```

Jeder Eintrag enthält `id`, `name`, `type`, `path`, `size`, `updated_at`.

---

## Schritt 4 – Download und HEAD

```bash
FILE_ID=<id>

# Metadaten ohne Download
curl -sI -H "Authorization: Bearer $TOKEN" \
  "$HOST/api/entries/$FILE_ID/content" | grep -iE 'content-length|etag|accept-ranges'

# Kompletter Download
curl -s -H "Authorization: Bearer $TOKEN" \
  "$HOST/api/entries/$FILE_ID/content" -o kopie.txt
cat kopie.txt
```

---

## Schritt 5 – Unterbrochenen Download fortsetzen (Range)

```bash
# Nur die ersten 5 Bytes
curl -s -H "Authorization: Bearer $TOKEN" -H 'Range: bytes=0-4' \
  "$HOST/api/entries/$FILE_ID/content" -o teil1.bin
xxd teil1.bin        # zeigt "Zeile"

# Rest (optional bis ans Ende)
curl -s -H "Authorization: Bearer $TOKEN" -H 'Range: bytes=5-' \
  "$HOST/api/entries/$FILE_ID/content" -o teil2.bin

cat teil1.bin teil2.bin   # ergibt wieder den ganzen Inhalt
```

Ein Range-Request liefert `206 Partial Content` mit `Content-Range`.

---

## Schritt 6 – Umbenennen und Verschieben

```bash
# Umbenennen
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"bericht-2026.txt"}' \
  "$HOST/api/entries/$FILE_ID"

# Zurück in den übergeordneten Ordner verschieben
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"parent_id\":\"$DOCS\"}" \
  "$HOST/api/entries/$FILE_ID/move"
```

Beide Operationen sind reine Metadaten-Änderungen – es werden keine Daten kopiert.

---

## Schritt 7 – Kopieren

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"parent_id\":\"$DOCS\",\"name\":\"bericht-kopie.txt\"}" \
  "$HOST/api/entries/$FILE_ID/copy"
```

Die Kopie teilt denselben Blob (Deduplizierung), erhält aber eine eigene Version.

---

## Schritt 8 – Überschreiben erzeugt eine neue Version

```bash
printf 'neuer Inhalt\n' > report.txt

curl -s -X PUT "$HOST/api/uploads/simple?parent_id=$DOCS&name=bericht-kopie.txt" \
  -H "Authorization: Bearer $TOKEN" --data-binary @report.txt
```

Derselbe Name im selben Ordner → neue Version; `size`/`sha256` ändern sich.

---

## Schritt 9 – Löschen (Papierkorb)

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" "$HOST/api/entries/$FILE_ID"
```

Der Eintrag verschwindet aus dem Listing, wird aber erst nach 30 Tagen endgültig vom
Worker entfernt.

---

## Zusammenfassung

| Aktion | Endpunkt |
|---|---|
| Ordner | `POST /api/folders` |
| Upload | `PUT /api/uploads/simple` |
| Liste | `GET /api/entries?parent_id=…` |
| Download | `GET /api/entries/{id}/content` |
| Resume | `Range: bytes=…` |
| Umbenennen | `PATCH /api/entries/{id}` |
| Verschieben | `POST /api/entries/{id}/move` |
| Kopieren | `POST /api/entries/{id}/copy` |
| Löschen | `DELETE /api/entries/{id}` |

Weiter mit [Tutorial 3 – FTPS](03-ftps-mit-filezilla-und-curl.md).
