# Mitwirken (Contributing)

Danke für dein Interesse an IFS! Bitte beachte die folgenden Hinweise.

## Entwicklungsumgebung

Voraussetzungen: Python ≥ 3.11 (getestet mit 3.11/3.12), optional Docker für den
vollständigen Stack.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -e ".[dev]"
```

Die Tests laufen ohne externe Dienste (SQLite + Fakes für den Objektspeicher):

```bash
python -m pytest                # parallel (Standard, pytest-xdist)
python -m pytest -n0            # seriell, z. B. zum Debuggen
python -m ruff check src tests
```

## Projektstruktur

```
src/ifs/            Anwendung (api/, core/, ftp/, web/)
tests/              Pytest-Suite
migrations/         Alembic + SQL-Skripte
docs/               Handbücher (Installation, API, Sicherheit, Betrieb …)
tutorials/          Schritt-für-Schritt-Anleitungen
deploy/             Betriebs-/Deploy-Hilfsskripte
```

## Arbeitsweise

1. Branch von `main` abzweigen (z. B. `feat/…`, `fix/…`).
2. Änderungen klein und fokussiert halten; bestehende Muster/Schreibweise übernehmen.
3. Für Verhalten **Tests** ergänzen (`tests/`), für Features **Doku** aktualisieren
   (`README.md`, `docs/`, `FEATURES.md`, ggf. `CHANGELOG.md`).
4. Vor dem PR ausführen:
   - `python -m ruff check src tests`
   - `python -m pytest`
5. PR mit klarer Beschreibung (Was/Warum, Referenz auf Issues, Test-Hinweise).

## Sicherheit

- Keine Secrets/`.env`, Zertifikate oder Host-spezifischen Daten committen.
- Sensible Änderungen (Auth, Rechte, Uploads, Freigaben) bitte mit Bedrohungsmodell
  und Tests beschreiben.
- Sicherheitslücken **nicht** als öffentliches Issue melden, sondern gemäß
  [`SECURITY.md`](SECURITY.md).

## Commit-Stil

Kurze, imperative Betreffzeile (z. B. `fix: share requires read permission`).
Conventional-Commit-Präfixe sind willkommen, aber nicht verpflichtend.
