# `deploy/` — Betriebs- und Hilfsskripte

Diese Skripte sind für den Betrieb auf dem Test-/Deploy-Host gedacht, nicht für
die lokale Entwicklung. Aufruf grundsätzlich mit `bash` (die Dateien sind im
Repository nicht als ausführbar markiert):

```bash
bash deploy/up.sh
```

## Kanonische Skripte

Diese Skripte werden unterstützt und in der Dokumentation referenziert.

| Skript | Zweck |
|---|---|
| `up.sh` | Image bauen und Compose-Stack starten |
| `down.sh` | Stack stoppen (Volumes bleiben erhalten) |
| `status.sh` | Compose-Status, Logs, Health und Admin-Zugangsdaten anzeigen |
| `rebuild.sh` | Image neu bauen und `api`/`ftp`/`worker` neu starten (mit Zeitmessung) |
| `prepare.sh` | Projekt im Home-Verzeichnis entpacken, `.env` und FTPS-Zertifikat erzeugen |
| `setup-seaweedfs.sh` | S3-Zugangsdaten und Storage-UI-Login erzeugen |
| `preflight-s3.sh` | S3-Endpoint, Zugriff, Range und Multipart prüfen (ohne Eingriff) |
| `apply-migrations.sh` | Alle SQL-Migrationen aus `migrations/sql/` anwenden |
| `apply-permissions.sh` | Rollen→Berechtigungen phasenweise (0011+0012; Cleanup nur mit `IFS_PERMISSIONS_CLEANUP=1`) |
| `verify-permissions.sh` | Read-only-Invarianten nach der Berechtigungs-Migration prüfen |
| `verify.sh` | End-to-End-Smoke-Test gegen die laufende API |
| `nginx-ifs.conf.example` | Beispielkonfiguration für einen nginx-Reverse-Proxy |

Viele der älteren Skripte wechseln fest nach `/home/ifs/ifs` und erwarten den
Stack dort. `setup-seaweedfs.sh` und `preflight-s3.sh` ermitteln das
Projektverzeichnis dagegen relativ zum Skript und funktionieren auch im lokalen
Checkout.

## `bench/` — Messungen und Tuning

Performance- und Durchsatzmessungen (`bench-*`, `profile-*`, `tune-*`) sowie die
zugehörigen Aufräumskripte. Die Skripte sind **nicht** Teil des normalen
Betriebs, sondern dokumentieren Messreihen. Ergebnisse und Zusammenhänge stehen
in [`docs/http-transfers.md`](../docs/http-transfers.md) und
[`docs/ftps.md`](../docs/ftps.md).

## `archive/` — historische Einmal-Skripte

`diag-*`, `verify-*`, `check-*`, `rollout-*`, `smoke-*`, `cleanup-*`,
Migrations-Einzelaufrufe (`apply-0004.sh` … `apply-0009.sh`) und ähnliche
Werkzeuge aus konkreten Reviews. Sie sind **nicht** gepflegt, enthalten
host-spezifische Annahmen und dienen nur noch als Nachschlagewerk. Für neue
Prüfungen die Pytest-Suite (`tests/`) oder `verify.sh` verwenden.

Die Einzelaufrufe `apply-0004.sh` … `apply-0009.sh` sind durch
`apply-migrations.sh` ersetzt.

## Nicht im Repository

`known_hosts` ist host-spezifisch und per `.gitignore` ausgeschlossen.
Zugangsdaten liegen ausschließlich in Umgebungsvariablen (siehe
`scripts/deploy_common.py`).
