# Release-Anleitung

Diese Anleitung beschreibt, wie das Projekt erstmals in ein GitHub-Repository
überführt und ein **Pre-Release** veröffentlicht wird.

> Hinweis: In der Entwicklungs-Workspace ist `git`/`gh` ggf. nicht installiert.
> Führe die Schritte auf einem Rechner mit installiertem
> [Git](https://git-scm.com/) und optional der [GitHub CLI](https://cli.github.com/) aus.

---

## 0. Vorbereitung (einmalig)

1. **Platzhalter ersetzen:** `OWNER/REPO` durch den echten GitHub-Pfad ersetzen in
   - `README.md` (Badges),
   - `CHANGELOG.md` (Release-Link),
   - `.github/ISSUE_TEMPLATE/config.yml` (Links).
2. **Secrets prüfen:** Es dürfen **keine** Secrets im Repo liegen. `.gitignore`
   schließt `.env`, `certs/`, `ADMIN_CREDENTIALS.txt`, `seaweedfs/s3.json`,
   `seaweedfs/Caddyfile`, `deploy/known_hosts` und `for-agents/` aus.
   Kontrolle:
   ```bash
   git status --ignored
   git grep -nE "(password|secret|BEGIN (RSA|OPENSSH))" -- . ':!*.example' || true
   ```
3. **Version:** steht in `pyproject.toml` und `src/ifs/__init__.py` (aktuell `0.1.4`);
   vor einem Release in beiden Dateien angleichen.

---

## 1. Lokales Repository initialisieren

```bash
cd <projektordner>
git init -b main
git add .
git status            # sicherstellen, dass nichts Sensibles dabei ist
git commit -m "chore: initial import (IFS 0.1.0)"
```

## 2. GitHub-Repository anlegen und pushen

Variante A – GitHub CLI:

```bash
gh repo create OWNER/REPO --private --source . --remote origin --push
# oder --public
```

Variante B – Web-UI: leeres Repo `OWNER/REPO` anlegen, dann:

```bash
git remote add origin git@github.com:OWNER/REPO.git   # oder https://…
git push -u origin main
```

Nach dem ersten Push läuft **CI** (`.github/workflows/ci.yml`) auf Python 3.11/3.12.

## 3. Pre-Release taggen und veröffentlichen

```bash
git tag -a v0.1.0 -m "IFS 0.1.0 (Pre-Release)"
git push origin v0.1.0
```

Der Tag stößt `.github/workflows/release.yml` an. Der Workflow ist in zwei Jobs
getrennt:

1. **`build`** (nur Leserechte, `contents: read`): prüft zuerst, dass der Tag auf
   `main` liegt (sonst schlägt der Lauf fehl), baut dann **sdist + wheel** und
   erzeugt eine `SHA256SUMS`-Datei. Die Artefakte werden als `dist`-Artifact
   hochgeladen. Es wird **kein** pip-Cache verwendet, damit der Release-Build
   cachefrei und reproduzierbar ist; Actions und Build-Toolchain (`build==1.6.1`)
   sind gepinnt.
2. **`release`** (`contents: write`, `id-token: write`, `attestations: write`):
   lädt das `dist`-Artifact herunter, hängt bei **öffentlichen** Repos eine
   Build-Provenance-Attestierung an und erstellt das **Draft-Release** mit
   generierten Notes inklusive `SHA256SUMS`.

Der `release`-Job läuft im GitHub-Environment **`release`**. Es wird empfohlen,
dort einen **Required Reviewer (Approval)** zu hinterlegen, damit die
Veröffentlichung manuell freigegeben werden muss.

> Voraussetzung: Der Release-Tag (`vX.Y.Z`) muss auf dem `main`-Branch liegen.

Release als Pre-Release veröffentlichen:

```bash
# Variante A: direkt als Pre-Release (ohne vorherigen Draft)
gh release create v0.1.0 --prerelease --generate-notes

# Variante B: bestehenden Draft prüfen und als Pre-Release publizieren
gh release edit v0.1.0 --draft=false --prerelease
```

Alternativ im GitHub-Web: Draft öffnen → „Set as a pre-release" aktivieren →
„Publish release".

---

## 4. Versions-/Release-Checkliste (für spätere Releases)

- [ ] `pyproject.toml`: `version`
- [ ] `src/ifs/__init__.py`: `__version__`
- [ ] `CHANGELOG.md`: neuer Abschnitt + Link
- [ ] `python -m ruff check src tests` und `python -m pytest` grün
- [ ] Tag `vX.Y.Z` (Pre-Release: `-rc.N` oder als Pre-release markiert)
- [ ] Tag liegt auf `main`
- [ ] Environment `release` mit Required Reviewer (Approval) konfiguriert
- [ ] Draft-Release: `SHA256SUMS` und ggf. Build-Provenance vorhanden
- [ ] Docker-Images/Deployment auf die Version verweisen

## 5. CI-Erwartungen

- `ruff check src tests` → sauber
- `pytest` → alle Tests grün (SQLite + Fakes, keine externen Dienste nötig)
- `secret-scan` (gitleaks) → keine Secrets im Verlauf (nur bei öffentlichen Repos;
  private Repos bräuchten ein `GITLEAKS_LICENSE`-Secret)
