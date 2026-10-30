#!/usr/bin/env bash
# Diagnose des IFS-Stacks (als root via sudo).
set -uo pipefail
cd /home/ifs/ifs
COMPOSE="docker compose"

echo "== api logs (letzte 120) =="
$COMPOSE logs --tail=120 api
echo "== ftp logs (letzte 20) =="
$COMPOSE logs --tail=20 ftp
echo "== worker logs (letzte 20) =="
$COMPOSE logs --tail=20 worker
echo "== Tabellen =="
$COMPOSE exec -T db psql -U ifs -d ifs -c '\dt' || true
echo "== API health =="
curl -sS -m 5 http://127.0.0.1:8000/healthz || true
echo
echo "== s3mock HTTP-Status =="
curl -sS -m 5 -o /dev/null -w 'bucket_ifs=%{http_code}\n' http://127.0.0.1:9090/ifs || true
echo "== S3-Verbindung aus dem api-Container =="
$COMPOSE exec -T api python -c "import boto3,os; \
c=boto3.client('s3',endpoint_url=os.environ.get('IFS_S3_ENDPOINT_URL'),\
aws_access_key_id=os.environ.get('IFS_S3_ACCESS_KEY'),\
aws_secret_access_key=os.environ.get('IFS_S3_SECRET_KEY'),region_name='us-east-1'); \
print('buckets:', [b['Name'] for b in c.list_buckets()['Buckets']])" 2>&1 | tail -n 5 || true
