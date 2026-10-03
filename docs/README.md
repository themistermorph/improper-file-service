# IFS – Dokumentation

Willkommen zur Dokumentation des **IFS** (webbasiertes Dateisystem mit HTTP(S)- und
FTPS-Zugriff auf einen S3-kompatiblen Objektspeicher).

Diese Dokumentation ist in zwei Teile gegliedert:

- **Handbücher** (`docs/`) – Referenz und Hintergrundwissen.
- **Tutorials** (`tutorials/`) – Schritt-für-Schritt-Anleitungen zum Mitmachen.

---

## Ich möchte …

| Ziel | Dokument |
|---|---|
| IFS zum ersten Mal starten | [Tutorial 1 – Schnellstart](../tutorials/01-schnellstart.md) |
| Dateien per HTTP hoch-/herunterladen | [Tutorial 2 – Dateien über HTTP](../tutorials/02-dateien-ueber-http.md) |
| Dateien per FTPS übertragen | [Tutorial 3 – FTPS](../tutorials/03-ftps-mit-filezilla-und-curl.md) |
| Große/abbrechende Uploads fortsetzen | [Tutorial 4 – Resumable Uploads](../tutorials/04-resumable-uploads.md) |
| Benutzer, Gruppen und Rechte setzen | [Tutorial 5 – Benutzer & Rechte](../tutorials/05-benutzer-gruppen-rechte.md) |
| Dateien mit Dritten teilen | [Tutorial 6 – Freigabelinks](../tutorials/06-freigabelinks.md) |
| Dateien öffentlich veröffentlichen | [Tutorial 11 – Veröffentlichungen](../tutorials/11-veroeffentlichungen.md) |
| Backups erstellen und wiederherstellen | [Tutorial 7 – Backup & Restore](../tutorials/07-backup-restore.md) |
| IFS aus einer eigenen Anwendung ansprechen | [Tutorial 8 – API-Anbindung](../tutorials/08-api-anbindung-python.md) |
| IFS produktiv betreiben | [Tutorial 9 – Produktiv-Deployment](../tutorials/09-produktiv-deployment.md) |
| Einen Fehler untersuchen | [Tutorial 10 – Troubleshooting](../tutorials/10-troubleshooting.md) |
| Die REST-API nachschlagen | [API-Referenz](api-referenz.md) |
| FTPS-Befehle und Clients verstehen | [FTPS-Handbuch](ftps.md) |
| Die Datenhaltung verstehen | [Datenmodell](datenmodell.md) |
| Alle Einstellungen finden | [Konfiguration](konfiguration.md) |
| Sicherheitsentscheidungen nachvollziehen | [Sicherheit](sicherheit.md) |
| Betrieb, Backup, Monitoring | [Betrieb](betrieb.md) |
| Am Code mitarbeiten | [Entwicklung](entwicklung.md) |
| Änderungen nachlesen | [`../CHANGELOG.md`](../CHANGELOG.md) |
| Sicherheitslücke melden | [`../SECURITY.md`](../SECURITY.md) |
| Beitragen / Release | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) · [`../RELEASING.md`](../RELEASING.md) |

---

## Grundidee in drei Sätzen

Der **Namespace** (Ordner, Dateien, Versionen, Rechte) lebt in **PostgreSQL**. Die
Dateiinhalte liegen als unveränderliche, über ihren SHA-256 adressierte **Blobs** in
**S3**. HTTP(S) und FTPS sind austauschbare **Adapter** über einem gemeinsamen Kern –
dadurch gelten dieselben Benutzer, Rechte und Metadaten auf allen Zugriffswegen.

---

## Planungsdokumente

- [`../ANFORDERUNGEN.md`](../ANFORDERUNGEN.md) – Anforderungskatalog
- [`../ARCHITEKTUR.md`](../ARCHITEKTUR.md) – Architekturkonzept
- [`../FEATURES.md`](../FEATURES.md) – Featurebeschreibung Ausbaustufe 2
- [`../README.md`](../README.md) – Projektüberblick und Schnellstart
