#!/usr/bin/env bash
# Bereitet das IFS-Projekt im Home-Verzeichnis vor (läuft als Benutzer ifs).
# - entpackt ifs.zip
# - entfernt das mitgelieferte Windows-.venv
# - erzeugt .env mit zufälligen Secrets
# - erzeugt ein selbstsigniertes FTPS-Zertifikat
set -euo pipefail

cd "$HOME"

echo "== Entpacken =="
rm -rf ifs
unzip -q -o ifs.zip
rm -rf ifs/.venv ifs/.pytest_cache ifs/.ruff_cache
cd ifs

echo "== Abhängigkeiten prüfen (pyopenssl für FTPS) =="
if ! grep -q 'pyopenssl' pyproject.toml; then
  sed -i 's#"pyftpdlib>=1.5.10",#"pyftpdlib>=1.5.10",\n    "pyopenssl>=24.0",#' pyproject.toml
  echo "pyopenssl ergänzt."
else
  echo "pyopenssl bereits vorhanden."
fi
grep -A12 '^dependencies' pyproject.toml || true

echo "== .env erzeugen =="
JWT="$(openssl rand -hex 32)"
ADMIN_PW="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-20)"
sed -e "s|^IFS_JWT_SECRET=.*|IFS_JWT_SECRET=${JWT}|" \
    -e "s|^IFS_ADMIN_PASSWORD=.*|IFS_ADMIN_PASSWORD=${ADMIN_PW}|" \
    .env.example > .env
chmod 600 .env
{
  echo "username=admin"
  echo "password=${ADMIN_PW}"
} > ADMIN_CREDENTIALS.txt
chmod 600 ADMIN_CREDENTIALS.txt

echo "== FTPS-Zertifikat erzeugen =="
mkdir -p certs
FTPS_CN="${IFS_FTP_CN:-localhost}"
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/ftps.key -out certs/ftps.crt -days 365 \
  -subj "/CN=${FTPS_CN}" >/dev/null 2>&1
chmod 644 certs/ftps.crt
chmod 600 certs/ftps.key

echo "== Fertig =="
ls -la
echo "PREPARE_OK"
