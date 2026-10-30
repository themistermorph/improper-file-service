#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
TEST=/home/ifs/ifs/.seaweed-write-test.bin

echo "== SATA-Link / Kernel =="
dmesg 2>/dev/null | grep -iE 'ata[0-9].*(link up|SATA|failed|error)|sda.*(error|reset)' | tail -n 20 || true
for f in /sys/class/ata_link/link*/sata_spd; do [ -e "$f" ] && echo "$f=$(cat "$f")"; done

echo
echo "== SMART =="
if command -v smartctl >/dev/null 2>&1; then
  sudo smartctl -H -i -A /dev/sda 2>&1 | grep -iE 'Model|Firmware|Serial|SMART overall|Reallocated|Wear|Program|Erase|Power_On|Temperature|Percentage|Media_Wearout|Available_Reservd|Total_LBAs' | head -n 30
else
  echo "smartctl nicht installiert"
fi

echo
echo "== TRIM =="
systemctl is-active fstrim.timer 2>/dev/null || true
systemctl list-timers fstrim.timer --no-pager 2>/dev/null | head -n 3 || true

echo
echo "== Ext4 / Mount =="
findmnt -no SOURCE,FSTYPE,OPTIONS /
cat /sys/fs/ext4/sda2/discard 2>/dev/null || true

echo
echo "== Raw Write (direct, fsync, 256 MiB x3) =="
for i in 1 2 3; do
  sync
  dd if=/dev/zero of="$TEST" bs=1M count=256 oflag=direct conv=fdatasync 2>&1 | tail -n 1
  rm -f "$TEST"
done

echo
echo "== Raw Write (buffered, fsync, 256 MiB) =="
sync
dd if=/dev/zero of="$TEST" bs=1M count=256 conv=fdatasync 2>&1 | tail -n 1
rm -f "$TEST"

echo
echo "== Raw Read (direct, 256 MiB) =="
sync
dd if=/dev/zero of="$TEST" bs=1M count=256 oflag=direct 2>/dev/null || true
sync
dd if="$TEST" of=/dev/null bs=1M count=256 iflag=direct 2>&1 | tail -n 1
rm -f "$TEST"

echo
echo "== Memory / Swap =="
free -h
cat /proc/pressure/memory 2>/dev/null || true
cat /proc/pressure/io 2>/dev/null || true
echo DIAG_SEAWEED_WRITE_DONE
