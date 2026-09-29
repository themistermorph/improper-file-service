# Tutorial 5 – Benutzer, Gruppen und Rechte

**Ziel:** Einen Benutzer und eine Gruppe anlegen, Ordner freigeben, Zugriffe prüfen –
sowohl über HTTP als auch über FTPS.

**Voraussetzungen:** laufendes IFS; Admin-Token.

---

## 1. Modell in Kürze

- Jeder Eintrag hat einen **Besitzer** (implizit alle Rechte).
- **ACLs** vergeben Rechte an Benutzer oder Gruppen.
- Rechte **vererben** sich: ein Recht auf einem Ordner gilt für alle Nachfahren.
- Rechte-Bitmaske: `read`, `write`, `delete`, `share`, `admin`.
- **ACLs setzen** dürfen nur globale Admins; **Freigaben** (`share`) darf der Besitzer.

---

## 2. Benutzer und Gruppe anlegen

```bash
HOST=http://localhost:8000
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
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
# Zwei Ordner in der Wurzel
SHARED=$(curl -s -X POST $HOST/api/folders -H "$AUTH" -H "$JSON" \
  -d '{"name":"team-share"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

PRIVATE=$(curl -s -X POST $HOST/api/folders -H "$AUTH" -H "$JSON" \
  -d '{"name":"nur-admin"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# Wurzel-ID ermitteln (parent_id des neuen Ordners)
ROOT=$(curl -s -H "$AUTH" "$HOST/api/entries/$SHARED" \
  | python -c "import sys,json;print(json.load(sys.stdin)['parent_id'])")
echo "shared=$SHARED private=$PRIVATE root=$ROOT"

# Gruppe "team": read auf der Wurzel (zum Navigieren) + read/write auf team-share
curl -s -X POST $HOST/api/entries/$ROOT/acl -H "$AUTH" -H "$JSON" \
  -d "{\"principal_type\":\"group\",\"principal_id\":\"$GROUP\",\"perms\":[\"read\"]}" \
  -o /dev/null -w "ACL Wurzel: %{http_code}\n"

curl -s -X POST $HOST/api/entries/$SHARED/acl -H "$AUTH" -H "$JSON" \
  -d "{\"principal_type\":\"group\",\"principal_id\":\"$GROUP\",\"perms\":[\"read\",\"write\"]}" \
  -o /dev/null -w "ACL team-share: %{http_code}\n"
```

---

## 4. Als alice anmelden und prüfen

```bash
ALICE_TOKEN=$(curl -s -X POST $HOST/api/auth/login -H "$JSON" \
  -d '{"username":"alice","password":"alice-geheim"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
ALICE_AUTH="Authorization: Bearer $ALICE_TOKEN"

# Wurzel auflisten (erlaubt, da read auf Wurzel)
curl -s -H "$ALICE_AUTH" "$HOST/api/entries"

# In team-share schreiben (erlaubt)
echo "von alice" > alice.txt
curl -s -X PUT "$HOST/api/uploads/simple?parent_id=$SHARED&name=alice.txt" \
  -H "$ALICE_AUTH" --data-binary @alice.txt

# In "nur-admin" schreiben (verweigert → 403)
curl -s -o /dev/null -w "Schreiben in nur-admin: %{http_code}\n" \
  -X PUT "$HOST/api/uploads/simple?parent_id=$PRIVATE&name=x.txt" \
  -H "$ALICE_AUTH" --data-binary @alice.txt
```

Erwartung: das Schreiben in `team-share` gelingt (201), das in `nur-admin` wird mit
**403** abgelehnt.

---

## 5. Dieselben Rechte über FTPS

```bash
lftp -u alice,alice-geheim ftps://localhost <<'EOF'
ls
cd /team-share
put alice.txt
cd /nur-admin
EOF
```

`lftp` kann `team-share` lesen/schreiben, `nur-admin` verweigert der Server.

---

## 6. Rechte entziehen / Freigabe widerrufen

Es gibt aktuell keinen API-Endpunkt zum Löschen einzelner ACL-Einträge. Zum Entzug:

- den Benutzer aus der Gruppe entfernen (DB-seitig/Adminaufgabe), oder
- eine restriktivere Struktur verwenden, oder
- den Eintrag in den Papierkorb verschieben.

> Ein `DELETE /api/entries/{id}/acl/{acl_id}` ist als Erweiterung vorgesehen.

---

## 7. Hinweise und bekannte Grenzen

- **Listing:** Wer `read` auf einem Ordner hat, sieht die **Namen** aller Kinder. Der
  Zugriff auf Inhalte wird weiterhin pro Eintrag geprüft.
- **Vererbung:** Rechte auf einem Ordner gelten für den gesamten Teilbaum.
- **Admin:** Die globale Adminrolle umgeht alle ACLs.
- **FTP = HTTP:** Es gibt keinen separaten Rechtebestand; FTP-Login und -Zugriffe nutzen
  exakt dasselbe Modell.

Weiter mit [Tutorial 6 – Freigabelinks](06-freigabelinks.md).
