#!/usr/bin/env bash
# Verifiziert die Fixes B1, B3, B8, B15, B16 aus dem fünften Review.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
py() { python3 -c "$1"; }
jget() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
J='Content-Type: application/json'
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
login() { curl -s -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | jget "['access_token']"; }

AUTH="Authorization: Bearer $(login "$USER" "$PASS")"
SUF="$$"
ADMIN_ROOT="$(curl -s -H "$AUTH" "$BASE/api/root" | jget "['id']")"

echo "== B1: FTP-Download >64 KiB vollständig =="
python3 - "$USER" "$PASS" <<'PY'
import ftplib, io, ssl, sys
user, password = sys.argv[1], sys.argv[2]
data = bytes((i * 7 + 13) % 256 for i in range(120 * 1024))
ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
try:
    ftp.connect("127.0.0.1", 21, timeout=20)
    ftp.login(user, password)
    ftp.prot_p()
    ftp.storbinary("STOR r5-big.bin", io.BytesIO(data))
    out = bytearray()
    ftp.retrbinary("RETR r5-big.bin", out.extend)
    ok = bytes(out) == data
    print(f"  upload={len(data)} download={len(out)} vollstaendig={ok} (erwartet 122880/True)")
    assert ok, "FTP-Download unvollstaendig"
    ftp.delete("r5-big.bin")
    print("B1_FTP_OK")
finally:
    try:
        ftp.quit()
    except Exception:
        ftp.close()
PY

echo "== B3: Ordner-Kopie enthält Inhalte =="
SRC="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$J" -d '{"name":"r5-copy-src"}' | jget "['id']")"
curl -s -o /dev/null -X PUT "$BASE/api/uploads/simple?parent_id=$SRC&name=a.txt" -H "$AUTH" --data-binary 'inhalt'
COPY="$(curl -s -X POST "$BASE/api/entries/$SRC/copy" -H "$AUTH" -H "$J" -d "{\"parent_id\":\"$ADMIN_ROOT\",\"name\":\"r5-copy-dst\"}" | jget "['id']")"
NAMES="$(curl -s -H "$AUTH" "$BASE/api/entries?parent_id=$COPY" | py 'import sys,json;print([e["name"] for e in json.load(sys.stdin)])')"
echo "  Inhalt der Kopie: $NAMES (erwartet ['a.txt'])"

echo "== B8: Admin darf fremde Entry-Rollenzuweisung nicht entfernen =="
ALICE_NAME="r5a-$SUF"
ALICE_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$J" -d "{\"username\":\"$ALICE_NAME\",\"password\":\"geheim123\"}" | jget "['id']")"
ALICE="Authorization: Bearer $(login "$ALICE_NAME" geheim123)"
AF="$(curl -s -X POST "$BASE/api/folders" -H "$ALICE" -H "$J" -d '{"name":"r5-alice"}' | jget "['id']")"
VIEWER_ID="$(curl -s -H "$AUTH" "$BASE/api/roles?scope=resource" | py 'import sys,json;print(next(r["id"] for r in json.load(sys.stdin) if r["name"]=="viewer"))')"
AID="$(curl -s -X POST "$BASE/api/entries/$AF/roles" -H "$ALICE" -H "$J" -d "{\"role_id\":\"$VIEWER_ID\",\"principal_type\":\"user\",\"principal_id\":\"$ALICE_ID\"}" | jget "['id']")"
echo "  Admin DELETE (system): $(code -X DELETE "$BASE/api/roles/assignments/$AID" -H "$AUTH") (erwartet 403)"
echo "  Owner DELETE (entry):  $(code -X DELETE "$BASE/api/entries/$AF/roles/$AID" -H "$ALICE") (erwartet 204)"

echo "== B15: Audits share.revoke / acl.set =="
FILE="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$ADMIN_ROOT&name=r5.txt" -H "$AUTH" --data-binary 'x' | jget "['id']")"
STOK="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H "$J" -d "{\"entry_id\":\"$FILE\"}" | jget "['token']")"
curl -s -o /dev/null -X DELETE "$BASE/api/shares/$STOK" -H "$AUTH"
curl -s -o /dev/null -X POST "$BASE/api/entries/$FILE/acl" -H "$AUTH" -H "$J" -d "{\"principal_type\":\"user\",\"principal_id\":\"$ALICE_ID\",\"perms\":[\"read\"]}"
REV="$(curl -s -H "$AUTH" "$BASE/api/audit?action=share.revoke&limit=5" | py 'import sys,json;print(len(json.load(sys.stdin)))')"
ACL="$(curl -s -H "$AUTH" "$BASE/api/audit?action=acl.set&limit=5" | py 'import sys,json;print(len(json.load(sys.stdin)))')"
echo "  audit share.revoke=$REV, acl.set=$ACL (jeweils >=1)"

echo "== B16: Validierung + fremde Upload-Session =="
echo "  upload size=-1:          $(code -X POST "$BASE/api/uploads" -H "$AUTH" -H "$J" -d "{\"parent_id\":\"$ADMIN_ROOT\",\"name\":\"x.bin\",\"size\":-1}") (erwartet 422)"
echo "  share max_downloads=-1:  $(code -X POST "$BASE/api/shares" -H "$AUTH" -H "$J" -d "{\"entry_id\":\"$FILE\",\"max_downloads\":-1}") (erwartet 422)"
SESS="$(curl -s -X POST "$BASE/api/uploads" -H "$AUTH" -H "$J" -d "{\"parent_id\":\"$ADMIN_ROOT\",\"name\":\"sess.bin\"}" | jget "['id']")"
echo "  HEAD fremde Session:     $(code -I "$BASE/api/uploads/$SESS" -H "$ALICE") (erwartet 403)"
curl -s -o /dev/null -X DELETE "$BASE/api/uploads/$SESS" -H "$AUTH"

echo "== Aufräumen =="
for f in "$SRC" "$COPY"; do
  curl -s -o /dev/null -X DELETE "$BASE/api/entries/$f" -H "$AUTH"
  curl -s -o /dev/null -X DELETE "$BASE/api/trash/$f" -H "$AUTH"
done
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$AF" -H "$ALICE"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$AF" -H "$ALICE"
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FILE" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$FILE" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/users/$ALICE_ID" -H "$AUTH"
echo VERIFY_REVIEW5_DONE
