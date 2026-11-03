# Tutorial 5 – Benutzer, Gruppen und Rechte

**Ziel:** Einen Benutzer und eine Gruppe anlegen, Ordner freigeben, Zugriffe prüfen –
sowohl über HTTP als auch über FTPS.

**Voraussetzungen:** laufendes IFS; Admin-Token.

---

## 1. Modell in Kürze

- Jeder Eintrag hat einen **Besitzer** (implizit alle Eintragsrechte).
- **ACLs** vergeben Eintragsrechte an Benutzer oder Gruppen; **Systemrechte**
  (`audit`, `monitor`) werden separat je Benutzer/Gruppe vergeben.
- Rechte **vererben** sich: ein Recht auf einem Ordner gilt für alle Nachfahren.
- Eintrags-Bitmaske: `read`, `write`, `delete`, `share`, `publish`, `admin`.
  Systemrechte: `audit`, `monitor`; `admin` kommt ausschließlich über die
  Adminrolle bzw. das Administrator-Flag.
- **ACLs setzen** darf, wer `admin` am Eintrag hat (Eigentümer bzw. Eintrags-Admin);
  Systemadmins haben **keinen** Dateizugriff auf fremde Wurzeln.
- **Per-User-Wurzel:** Jeder Benutzer hat ein eigenes Home. Freigegebene Einträge findest
  du als Empfänger unter **GET `/api/shared`** bzw. in der Web-UI „Mit mir geteilt".
- **Externe Freigaben** (`share`) darf der Besitzer bzw. wer `read`+`share` hat;
  **Veröffentlichen** verlangt `read`+`publish`.
- **Rollen** gibt es nur noch als systemweite `admin`-Rolle; fachliche Rollen
  wurden migriert und entfernt.

---

## 2. Benutzer und Gruppe anlegen

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$IFS_ADMIN_PASSWORD\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

# Benutzer alice
ALICE=$(curl -s -X POST $HOST/api/users -H "$AUTH" -H "$JSON" \
  -d '{"username":"alice","password":"alice-geheim","email":"alice@example.org"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# Gruppe team
GROUP=$(curl -s -X POST $HOST/api/groups -H "$AUTH" -H "$JSON" \
  -d '{"name":"team"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# alice in team aufnehmen
curl -s -X POST $HOST/api/groups/$GROUP/members -H "$AUTH" -H "$JSON" \
  -d "{\"user_id\":\"$ALICE\"}" -o /dev/null -w "Mitglied hinzugefügt: %{http_code}\n"

echo "alice=$ALICE group=$GROUP"
```

---

## 3. Ordner anlegen und freigeben

```bash
# Zwei Ordner im Home des Admins
SHARED=$(curl -s -X POST $HOST/api/folders -H "$AUTH" -H "$JSON" \
  -d '{"name":"team-share"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

PRIVATE=$(curl -s -X POST $HOST/api/folders -H "$AUTH" -H "$JSON" \
  -d '{"name":"nur-admin"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

echo "shared=$SHARED private=$PRIVATE"

# Gruppe "team": read/write auf team-share (Vererbung gilt für den Teilbaum)
curl -s -X POST $HOST/api/entries/$SHARED/acl -H "$AUTH" -H "$JSON" \
  -d "{\"principal_type\":\"group\",\"principal_id\":\"$GROUP\",\"perms\":[\"read\",\"write\"]}" \
  -o /dev/null -w "ACL team-share: %{http_code}\n"
```

Der Ordner liegt im Home des Admins. Alice erreicht ihn nicht über ihr eigenes Listing,
sondern über die Freigabe (nächster Abschnitt).

---

## 4. Als alice anmelden und prüfen

```bash
ALICE_TOKEN=$(curl -s -X POST $HOST/api/auth/login -H "$JSON" \
  -d '{"username":"alice","password":"alice-geheim"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
ALICE_AUTH="Authorization: Bearer $ALICE_TOKEN"

# Eigenes Home (leer, da neu) auflisten
curl -s -H "$ALICE_AUTH" "$HOST/api/entries"

# Freigaben für alice anzeigen (team-share erscheint hier)
curl -s -H "$ALICE_AUTH" "$HOST/api/shared"

# In team-share schreiben (erlaubt, über die Freigabe-ID)
echo "von alice" > alice.txt
curl -s -X PUT "$HOST/api/uploads/simple?parent_id=$SHARED&name=alice.txt" \
  -H "$ALICE_AUTH" --data-binary @alice.txt

# In "nur-admin" schreiben (verweigert → 403)
curl -s -o /dev/null -w "Schreiben in nur-admin: %{http_code}\n" \
  -X PUT "$HOST/api/uploads/simple?parent_id=$PRIVATE&name=x.txt" \
  -H "$ALICE_AUTH" --data-binary @alice.txt
```

Erwartung: `/api/shared` listet `team-share`; das Schreiben in `team-share` gelingt (201),
das in `nur-admin` wird mit **403** abgelehnt. In der Web-UI erscheint `team-share` im
Tab **„Mit mir geteilt"**.

---

## 5. Zugriffe über FTPS

FTP-Pfade beziehen sich immer auf das **eigene Home** des Benutzers. Alice kann dort
lesen/schreiben; fremde (geteilte) Ordner sind per FTP nicht eingebunden – diese erreichst
du über die Web-UI bzw. `/api/shared`.

```bash
lftp -u alice,alice-geheim ftps://localhost <<'EOF'
ls
mkdir meine-dateien
cd /meine-dateien
put alice.txt
ls
EOF
```

Das Anlegen/Schreiben im eigenen Home gelingt; FTPS nutzt exakt dasselbe Rechte-Modell
wie HTTP.

---

## 6. Rechte entziehen / Freigabe widerrufen

Einzelne Grants lassen sich jetzt gezielt entfernen:

```bash
# ACLs eines Eintrags auflisten (Eintrags-Admin)
curl -s -H "$AUTH" "$HOST/api/entries/$SHARED/acl" | python -m json.tool

# Grant exakt ersetzen (PUT) bzw. entfernen (DELETE)
curl -s -X PUT -H "$AUTH" -H "$JSON" \
  -d '{"principal_type":"group","principal_id":"'"$GROUP"'","permissions":["read"]}' \
  "$HOST/api/entries/$SHARED/acl"
curl -s -X DELETE -H "$AUTH" "$HOST/api/entries/$SHARED/acl/<acl-id>"
```

Alternativ: Benutzer aus der Gruppe entfernen oder den Eintrag in den
Papierkorb verschieben. Systemrechte entziehst du über
`PUT /api/users/{id}/permissions` bzw. `PUT /api/groups/{id}/permissions` mit
einer leeren Liste.

> `POST /api/entries/{id}/acl` bleibt kompatibel und vereinigt Bits; `PUT`
> ersetzt sie exakt.

---

## 7. Hinweise und bekannte Grenzen

- **Listing:** Wer `read` auf einem Ordner hat, sieht die **Namen** aller Kinder. Der
  Zugriff auf Inhalte wird weiterhin pro Eintrag geprüft.
- **Vererbung:** Rechte auf einem Ordner gelten für den gesamten Teilbaum.
- **Admin:** Systemadmins haben **keinen** Dateizugriff auf fremde Wurzeln; die
  systemweite Rolle `admin` gewährt nur Systemfunktionen (Verwaltung), keine Dateirechte.
- **FTP = HTTP:** Es gibt keinen separaten Rechtebestand; FTP-Login und -Zugriffe nutzen
  exakt dasselbe Modell.

Weiter mit [Tutorial 6 – Freigabelinks](06-freigabelinks.md).
