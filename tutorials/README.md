# Tutorials

Praktische Schritt-für-Schritt-Anleitungen. Jedes Tutorial ist möglichst eigenständig und
nennt die Voraussetzungen am Anfang.

| # | Tutorial | Voraussetzungen |
|---|---|---|
| 1 | [Schnellstart](01-schnellstart.md) | Docker + Compose |
| 2 | [Dateien über HTTP](02-dateien-ueber-http.md) | laufendes IFS, `curl` |
| 3 | [FTPS mit FileZilla und curl](03-ftps-mit-filezilla-und-curl.md) | laufendes IFS, FTPS-Client |
| 4 | [Resumable Uploads](04-resumable-uploads.md) | laufendes IFS, `curl`, Python |
| 5 | [Benutzer, Gruppen und Rechte](05-benutzer-gruppen-rechte.md) | Admin-Token |
| 6 | [Freigabelinks](06-freigabelinks.md) | laufendes IFS |
| 7 | [Backup & Restore](07-backup-restore.md) | Docker-Compose-Betrieb |
| 8 | [API-Anbindung mit Python](08-api-anbindung-python.md) | Python 3.11+ |
| 9 | [Produktiv-Deployment](09-produktiv-deployment.md) | Server, Domain, Zertifikate |
| 10 | [Troubleshooting](10-troubleshooting.md) | laufendes IFS |

> **Per-User-Wurzel:** Jeder Benutzer hat ein eigenes Home. Von anderen Benutzern
> freigegebene Einträge erscheinen in der Web-UI unter **„Mit mir geteilt"**
> (API: `GET /api/shared`) – siehe Tutorial 5.

**Konventionen in den Tutorials**

- `HOST` ist die Basis-URL, z. B. `http://localhost:8000`.
- `TOKEN` ist ein gültiger Bearer-Token aus dem Login (Login-Passwort: `$IFS_ADMIN_PASSWORD`).
- `ID`, `GROUP_ID` usw. sind Platzhalter für zurückgegebene UUIDs.
