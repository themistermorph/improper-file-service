# Datenbankmigrationen

`sql/` enthält die produktiven, idempotenten Migrationen. Sie werden in
Dateinamen-Reihenfolge über den Compose-Dienst `db` angewendet:

```bash
bash deploy/apply-migrations.sh
```

Das Basisschema neuer Installationen entsteht über `ifs init-db`
(`create_all`). Die SQL-Migrationen dienen dem Upgrade bestehender
Installationen.

## Neue Migration

1. Nächste freie Nummer wählen: `sql/0011_kurzer_name.sql`.
2. Nur idempotente Statements verwenden (`CREATE ... IF NOT EXISTS`,
   `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).
3. Gegen eine Testdatenbank anwenden: `bash deploy/apply-migrations.sh`.
4. Migration in [`docs/betrieb.md`](../docs/betrieb.md#7-upgrades-und-migrationen)
   und im [`CHANGELOG.md`](../CHANGELOG.md) erwähnen.
5. Wenn die Migration bestehende Daten voraussetzt (z. B. genau eine Wurzel je
   Benutzer vor `0006`), die Voraussetzung im Dateikopf dokumentieren.

Alembic wird bewusst nicht verwendet: `migrations/versions/` war leer, und das
Deployment hat schon immer die SQL-Dateien angewendet.
