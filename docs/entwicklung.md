# Entwicklung

Leitfaden für die Mitarbeit am IFS-Code: Projektstruktur, Konventionen, Tests und
Erweiterungspunkte.

---

## 1. Projektstruktur

```
src/ifs/
  main.py            FastAPI-App, Lifespan (Schema/Seed), Exception-Handler
  cli.py             Rollenstart + init-db/seed-admin
  config.py          pydantic-settings (Präfix IFS_)
  db.py              Engine/Session, create_all
  models.py          SQLAlchemy-2.0-Modelle
  security.py        Argon2id, JWT, Tokens
  errors.py          Domänenfehler mit HTTP-Status
  utils.py           UUIDv7, Pfadnormalisierung
  core/              Protokollunabhängiger Kern
    authz.py         Policy Decision Point
    namespace.py     Baum, Versionen, Rename/Move, Papierkorb
    content.py       S3-Blobs (CAS), Streaming, Presign
    uploads.py       Resumable Upload-Sessions
    quota.py         Verbrauch/Quota-Prüfung
    audit.py         Audit-Log
    events.py        Outbox
  api/               FastAPI-Router + Pydantic-Schemas
  ftp/               FTPS-Gateway (pyftpdlib) + virtuelles Dateisystem
  worker.py          Hintergrundjobs
  web/index.html     Web-Frontend
tests/               pytest (Kern, API, FTPS-End-to-End)
migrations/          Alembic
scripts/             Hilfsskripte
```

**Schichtenregel:** `core/` kennt kein FastAPI/pyftpdlib. Adapter (`api/`, `ftp/`)
rufen ausschließlich den Kern auf. So bleibt die Rechte-Gleichheit zwischen den
Protokollen technisch erzwungen.

---

## 2. Synchroner Kern

Der Kern ist bewusst **synchron** (SQLAlchemy 2.0 sync + boto3):

- FastAPI-Endpunkte sind überwiegend `def` und laufen im Threadpool; Streaming-Endpunkte
  sind `async` und rufen den synchronen Kern direkt auf.
- Das FTPS-Gateway (pyftpdlib ist synchron) nutzt **denselben** Kern ohne Duplikation.

Ein async-Umbau wäre ein isoliertes, größeres Vorhaben (eigener ADR) und erst bei sehr
hoher Nebenläufigkeit sinnvoll.

---

## 3. Lokales Setup

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"

# Tests und Lint
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
```

Die Tests laufen gegen **SQLite** mit temporärem Spool-Verzeichnis
(`tests/conftest.py`) und mocken S3. Der FTPS-Test erzeugt zur Laufzeit ein
selbstsigniertes Zertifikat und öffnet eine echte TLS-Verbindung.

---

## 4. Einen neuen API-Endpunkt hinzufügen

1. **Kernlogik** in `core/` implementieren (keine FastAPI-Typen!). Fehler als
   `IFSError`-Ableger aus `errors.py` werfen.
2. **Schema** in `api/schemas.py` ergänzen (Request/Response).
3. **Router** in `api/api/<bereich>.py` erweitern; `Depends(get_db)` und
   `Depends(get_current_user)` nutzen.
4. **Autorisierung**: immer `authz.authorize(db, user, action, entry)` aufrufen.
5. **Audit**: mutierende Operationen mit `audit.record(...)` protokollieren.
6. **Test** in `tests/` ergänzen.

Beispielgerüst:

```python
@router.post("/entries/{entry_id}/touch", response_model=EntryOut)
def touch_entry(entry_id: UUID, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> EntryOut:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "write", entry)
    entry.updated_at = utcnow()
    db.flush()
    audit.record(db, "entry.touch", actor_id=user.id, target_entry=entry.id,
                 protocol="http", ip=client_ip(request))
    return to_entry_out(db, entry)
```

---

## 5. Ein neues Recht hinzufügen

1. Bit in `core/authz.py` definieren und `ACTION_PERM`/`PERM_NAMES` ergänzen.
2. In den Adaptern die passende Aktion verwenden (`authz.authorize(...)` bzw.
   `_PERM_ACTIONS` im FTPS-Gateway).
3. Tests in `tests/test_authz.py`.
4. Dokumentation aktualisieren (`docs/sicherheit.md`, `docs/api-referenz.md`).

---

## 6. Migrationen

```bash
# Modelle in src/ifs/models.py ändern, dann:
alembic revision --autogenerate -m "kurze beschreibung"
alembic upgrade head
```

Für den schnellen Dev-Start genügt `IFS_AUTO_CREATE_SCHEMA=true`
(`python -m ifs.cli init-db`). In Produktion immer Alembic.

---

## 7. FTPS-Gateway erweitern

- Das virtuelle Dateisystem liegt in `ftp/fs.py` und implementiert die
  `AbstractedFS`-Schnittstelle von pyftpdlib (`listdir`, `stat`, `open`, `mkdir`, …).
- Uploads werden lokal gespoolt (`_UploadWriter`) und erst im Callback
  `on_file_received` über `finalize_received` in den Kern übernommen.
- Neue Kommandos: Methoden wie `ftp_XXX` in einem `IFSHandler` überschreiben; die
  Rechteprüfung läuft über `IFSAuthorizer.has_perm` und zusätzlich im FS.

---

## 8. Erweiterungspunkte (Roadmap)

| Vorhaben | Anknüpfungspunkt |
|---|---|
| Direkter S3-Multipart-Upload (kein lokales Spooling) | `core/uploads.py`, `core/content.py` |
| WebDAV | neuer Adapter `api/webdav.py` über denselben Kern |
| Virenscan | Worker + Outbox-Event `entry.written` |
| Volltextsuche | Postgres-FTS, später OpenSearch über Outbox |
| OIDC/SSO, MFA | `security.py`, `api/deps.py`, `models.Credential` |
| Versionierungs-UI, Papierkorb-UI | `api/entries.py` + `web/index.html` |

---

## 9. Konventionen

- **Sprache:** Code/Kommentare englisch oder knapp deutsch konsistent je Datei;
  Benutzertexte deutsch.
- **Typisierung:** `from __future__ import annotations`, vollständige Typ-Hinweise.
- **Formatierung:** `ruff` (Line-Length 100). Lint-Ausnahmen sind in `pyproject.toml`
  begründet.
- **Fehler:** Domänenfehler aus `errors.py`, nicht `HTTPException` im Kern.
- **IDs:** immer `uuid7()`.
- **Zeit:** immer `utcnow()` (timezone-aware).
- **Mutationsmuster:** Blob schreiben → eine DB-Transaktion → Outbox-Event.

---

## 10. Beitrags-Checkliste

- [ ] Kernlogik in `core/`, Adapter dünn
- [ ] Autorisierung an jeder mutierenden/lesenden Operation
- [ ] Audit-Eintrag bei Mutationen
- [ ] Tests ergänzt (`pytest`)
- [ ] `ruff check src tests` ohne Befund
- [ ] Dokumentation/API-Referenz aktualisiert
- [ ] Bei Schemaänderung: Alembic-Revision
