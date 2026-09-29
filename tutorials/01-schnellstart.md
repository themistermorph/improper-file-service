# Tutorial 1 – Schnellstart

**Ziel:** IFS in wenigen Minuten starten, anmelden und die erste Datei ablegen.

**Voraussetzungen:** Docker Engine und Docker Compose.

---

## Schritt 1 – Projekt vorbereiten

```bash
git clone <repository> ifs
cd ifs
cp .env.example .env
```

Öffne `.env` und ändere zwei Werte:

```dotenv
IFS_JWT_SECRET=bitte-einen-langen-zufaelligen-string-eintragen
IFS_ADMIN_PASSWORD=mein-starkes-passwort
```

> Für einen echten Zufallswert:
> `python -c "import secrets; print(secrets.token_urlsafe(48))"`

---

## Schritt 2 – FTPS-Zertifikat erzeugen

```bash
python scripts/gen-dev-cert.py certs
```

Erwartete Ausgabe:

```
Zertifikat erzeugt: certs/ftps.crt / certs/ftps.key
```

---

## Schritt 3 – Starten

```bash
docker compose up -d --build
docker compose ps
```

Warte, bis `db` als `healthy` erscheint. Beim ersten Start legt die API den Admin und
das Wurzelverzeichnis an. Als Objektspeicher dient standardmäßig **lokales SeaweedFS**
(persistentes Volume `s3-data`; Details in [Installation](../docs/installation.md)).

---

## Schritt 4 – Zugang prüfen

```bash
curl http://localhost:8000/healthz
```

Erwartete Antwort:

```json
{"status":"ok","users":1,"entries":1}
```

Öffne die Web-UI: <http://localhost:8000> und melde dich an
(Benutzer `admin`, das Passwort aus `.env`).

---

## Schritt 5 – Erste Datei über die UI

1. **Ordner anlegen:** im Feld „Neuer Ordner“ `projekte` eingeben → **Ordner anlegen**.
2. In den Ordner `projekte` wechseln (Klick auf den Namen).
3. **Datei hochladen:** **Datei auswählen**, eine Datei wählen, **Hochladen**.
4. Die Datei erscheint in der Liste. **Download** lädt sie wieder herunter.

---

## Schritt 6 – Erste Datei über die API

```bash
# 1. Anmelden und Token holen
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"mein-starkes-passwort"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Wurzelinhalt auflisten
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/entries

# 3. Datei in die Wurzel hochladen
echo "Hallo IFS" > hello.txt
curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
  --data-binary @hello.txt \
  "localhost:8000/api/uploads/simple?name=hello.txt"

# 4. Inhalt wieder ausgeben
curl -s -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/api/entries/<id-aus-schritt-3>/content"
```

---

## Schritt 7 – Stoppen

```bash
docker compose down        # Container stoppen/entfernen, Daten bleiben erhalten
```

Weiter mit [Tutorial 2 – Dateien über HTTP](02-dateien-ueber-http.md).
