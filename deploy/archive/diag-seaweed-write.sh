#!/usr/bin/env bash
# Diagnose SeaweedFS-Schreibpfad: Hardware, Disk, Docker-Volume, SeaweedFS-Status.
set -uo pipefail
cd /home/ifs/ifs

echo "== Host =="
hostname; uname -a
lscpu 2>/dev/null | grep -E 'Model name|^CPU\(s\)|Thread|Core|MHz' || true
free -h
uptime

echo
echo "== Blockgeräte =="
lsblk -o NAME,ROTA,SIZE,TYPE,TRAN,MODEL,MOUNTPOINTS 2>/dev/null || lsblk
echo "-- rotational --"
for dev in /sys/block/sd* /sys/block/nvme* /sys/block/vd*; do
  [ -e "$dev/queue/rotational" ] || continue
  echo "$(basename "$dev") rotational=$(cat "$dev/queue/rotational") scheduler=$(cat "$dev/queue/scheduler" 2>/dev/null)"
done
echo "-- mount/df --"
findmnt -no SOURCE,FSTYPE,OPTIONS / 2>/dev/null || mount | head -5
df -h / /var/lib/docker 2>/dev/null

echo
echo "== Docker-Volume / Daten =="
docker volume inspect ifs_s3-data 2>/dev/null | grep -E 'Mountpoint|Name' || true
du -sh /var/lib/docker/volumes/ifs_s3-data/_data 2>/dev/null || true
docker compose exec -T s3 sh -c 'echo "-- /data --"; df -h /data; du -sh /data/* 2>/dev/null | sort -h | tail -n 15' || true

echo
echo "== SeaweedFS-Status =="
curl -fsS 'http://127.0.0.1:9010/' >/dev/null 2>&1 || true
docker compose exec -T s3 sh -c '
  wget -qO- http://localhost:9333/vol/status 2>/dev/null | head -c 3000; echo
  echo "-- cluster --"
  wget -qO- http://localhost:9333/cluster/status 2>/dev/null | head -c 2000; echo
' || true

echo
echo "== SeaweedFS-Volume-Hilfe (Tuning-Flags) =="
docker compose exec -T s3 sh -c 'weed volume -h 2>&1 | grep -iE "max|disk|index|compact|write|sync|cache|memory|free|fileSize|volume" | head -n 80' || true

echo
echo "== SeaweedFS-Logs (letzte Fehler) =="
docker compose logs --tail=80 --no-color s3 2>/dev/null | grep -iE 'error|warn|slow|compact|full|disk|volume' | tail -n 40 || true
