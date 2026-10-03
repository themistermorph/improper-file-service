#!/usr/bin/env bash
# Prüft, dass Migration 0007 angewendet ist (neue Indizes vorhanden, alte entfernt).
set -uo pipefail
cd /home/ifs/ifs
PSQL="docker compose exec -T db psql -U ifs -d ifs -A -t"

echo "== erwartete neue Indizes (je 1) =="
for idx in \
  ix_entries_parent_trashed ix_entries_owner_trashed ix_entries_trashed_at \
  ix_versions_entry_seq ix_acl_entry_principal ix_role_assignments_principal_entry \
  ix_audit_log_ts ix_outbox_published_created ix_upload_sessions_status_updated \
  ix_shares_created_by_created; do
  n=$($PSQL -c "select count(*) from pg_indexes where indexname='$idx';")
  echo "  $n  $idx"
done

echo "== entfernte Einzelindizes (je 0) =="
for idx in ix_entries_parent_id ix_entries_owner_id ix_versions_entry_id ix_acl_entry_id ix_outbox_published_at; do
  n=$($PSQL -c "select count(*) from pg_indexes where indexname='$idx';")
  echo "  $n  $idx"
done
echo VERIFY_0007_DONE
