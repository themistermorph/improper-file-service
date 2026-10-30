#!/usr/bin/env bash
# Verifiziert v0.1.4: öffentlicher Anzeigename (Migration 0009) und
# rekursives FTPS-RMD (nicht-leere Ordner -> Papierkorb).
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
J='Content-Type: application/json'
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
py() { python3 -c "$1"; }
jget() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$J" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | jget "['access_token']")"
AUTH="Authorization: Bearer $TOKEN"
ROOT="$(curl -s -H "$AUTH" "$BASE/api/root" | jget "['id']")"

echo "===== A) Öffentlicher Anzeigename ====="
NAME="intern-$$.txt"
FILE="$(printf 'inhalt\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$ROOT&name=$NAME" \
  -H "$AUTH" --data-binary @- | jget "['id']")"
PUB="Oeffentlich-$$.txt"
echo "veroeffentlichen (public_name): $(code -X POST "$BASE/api/published/$FILE" -H "$AUTH" -H "$J" -d "{\"public_name\":\"$PUB\"}") (201)"
GAL="$(curl -s "$BASE/api/published")"
echo "Galerie-Anzeigename:            $(echo "$GAL" | py "import sys,json;d=json.load(sys.stdin);print([e['name'] for e in d if e['entry_id']=='$FILE'])")"
echo "Content-Disposition:            $(curl -sD - -o /dev/null "$BASE/api/published/$FILE/content" | tr -d '\r' | grep -i '^content-disposition' || echo '(keine)')"
NEW="Neu-$$.txt"
echo "PATCH Anzeigename:              $(code -X PATCH "$BASE/api/published/$FILE" -H "$AUTH" -H "$J" -d "{\"public_name\":\"$NEW\"}") (200)"
echo "Galerie nach PATCH:             $(curl -s "$BASE/api/published" | py "import sys,json;d=json.load(sys.stdin);print([e['name'] for e in d if e['entry_id']=='$FILE'])")"
echo "PATCH leer (Fallback intern):   $(code -X PATCH "$BASE/api/published/$FILE" -H "$AUTH" -H "$J" -d '{"public_name":null}') (200)"
echo "Galerie nach Fallback:          $(curl -s "$BASE/api/published" | py "import sys,json;d=json.load(sys.stdin);print([e['name'] for e in d if e['entry_id']=='$FILE'])")"
echo "zurueckziehen:                  $(code -X DELETE "$BASE/api/published/$FILE" -H "$AUTH") (204)"

echo "===== B) FTPS RMD rekursiv ====="
python3 - "$USER" "$PASS" <<'PY'
import ftplib, io, ssl, sys
user, password = sys.argv[1], sys.argv[2]
ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
try:
    ftp.connect("127.0.0.1", 21, timeout=60)
    ftp.login(user, password)
    ftp.prot_p()
    name = "rmdtest"
    try:
        ftp.mkd(name)
    except ftplib.all_errors:
        pass
    ftp.storbinary("STOR " + name + "/datei.txt", io.BytesIO(b"x" * 1024))
    try:
        ftp.mkd(name + "/unterordner")
    except ftplib.all_errors:
        pass
    print("RMD nicht-leer:                 ", end="")
    try:
        ftp.rmd(name)
        print("OK (250)")
    except ftplib.all_errors as exc:
        print(f"FEHLER ({exc})")
    print("danach NLST:                    ", end="")
    try:
        print(ftp.nlst())
    except ftplib.all_errors as exc:
        print(f"(leer/Fehler {exc})")
finally:
    try:
        ftp.quit()
    except Exception:
        ftp.close()
PY
echo "Ordner im Papierkorb?           $(curl -s "$BASE/api/trash" -H "$AUTH" | py "import sys,json;d=json.load(sys.stdin);print([e['name'] for e in d if e['name']=='rmdtest'])")"

echo "===== health/version ====="
curl -sS -m5 "$BASE/healthz"; echo
curl -sS -m5 "$BASE/version"; echo

echo "===== Aufräumen ====="
PURGE="$(curl -s "$BASE/api/trash" -H "$AUTH" | py "import sys,json;d=json.load(sys.stdin);print(' '.join(e['id'] for e in d if e['name']=='rmdtest'))")"
for id in $PURGE; do curl -s -o /dev/null -X DELETE "$BASE/api/trash/$id" -H "$AUTH"; done
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FILE" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$FILE" -H "$AUTH"
echo VERIFY_014_DONE
