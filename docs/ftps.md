# FTPS-Handbuch

IFS stellt einen **FTPS**-Dienst (FTP über TLS) bereit. Er nutzt **denselben**
Benutzer-, Rechte- und Metadatenbestand wie die HTTP-API. Es gibt **kein Plain-FTP**:
Klartext-Verbindungen werden abgelehnt.

---

## 1. Verbindungsdaten

| Parameter | Wert |
|---|---|
| Protokoll | FTP mit **explizitem TLS** (`AUTH TLS`) |
| Host | `IFS_FTP_HOST` (Standard: Rechner, auf dem der Dienst läuft) |
| Port | `IFS_FTP_PORT` (Standard `21`) |
| Verschlüsselung | `PROT P` – Steuer- **und** Datenkanal TLS |
| Modus | **Passiv** (`PASV`/`EPSV`) empfohlen |
| Passive-Ports | `IFS_FTP_PASSIVE_PORTS` (Standard `30000-30100`) |
| Login | Benutzername + Passwort aus dem IFS-Benutzerbestand |

> **Wichtig:** FTPS kann **nicht** über einen HTTP-Reverse-Proxy (Traefik/Caddy/nginx)
> veröffentlicht werden. Der Port muss per TCP erreichbar sein, ebenso die
> Passive-Portrange. Hinter NAT/Load-Balancer `IFS_FTP_MASQUERADE_ADDRESS` auf die
> nach außen sichtbare Adresse setzen.

---

## 2. Unterstützte Kommandos

| Kategorie | Kommandos |
|---|---|
| Verbindung/Auth | `AUTH TLS`, `PBSZ`, `PROT`, `USER`, `PASS`, `FEAT`, `OPTS UTF8 ON`, `SYST` |
| Navigation | `PWD`, `CWD`, `CDUP`, `TYPE` |
| Listen | `LIST`, `NLST`, `MLSD`, `MLST`, `SIZE`, `MDTM` |
| Download | `RETR`, `REST` (Resume-Offset) |
| Upload | `STOR`, `APPE` (Anhängen) |
| Verzeichnisse | `MKD`, `RMD` |
| Dateien | `DELE`, `RNFR`/`RNTO` |
| Passive | `PASV`, `EPSV` |

### Semantik gegenüber klassischem FTP

| Besonderheit | Verhalten |
|---|---|
| `STOR` auf vorhandenen Namen | erzeugt eine **neue Version** (überschreibt sichtbar) |
| `APPE` | lädt bestehenden Inhalt und hängt neu an |
| `REST` + `RETR`/`STOR` | setzt Offset; Neustart ab Position |
| `RNTO`/Move | reine Metadaten-Operation, kein Datenkopieren |
| `DELE`/`RMD` | Soft-Delete (Papierkorb) |
| `SITE CHMOD` | wird akzeptiert, hat aber keine Wirkung – Rechte nur über IFS |
| `STOU` | **nicht unterstützt** (Antwort `550`) |
| Symbolische Links | nicht vorhanden (virtuelles Dateisystem) |

---

## 3. Clients

### FileZilla

1. **Datei → Servermanager → Neuer Server**
2. Protokoll: **FTP – File Transfer Protocol**
3. Verschlüsselung: **Require explicit FTP over TLS**
4. Anmeldetyp: **Normal**, Benutzername + Passwort
5. Verbinden; das Zertifikat ggf. bestätigen.

### WinSCP

1. **Neue Verbindung**, Dateiprotokoll **FTP**
2. Verschlüsselung: **TLS/SSL Explicit encryption**
3. Hostname, Port 21, Benutzer, Passwort
4. Unter **Erweitert → Verbindung** ggf. Passive-Ports/Proxy prüfen.

### lftp (CLI)

```bash
lftp -u alice,geheim ftps://files.example.org
# im Prompt:
ls
put report.pdf
get report.pdf -o kopie.pdf
bye
```

### curl (CLI)

```bash
# Upload
curl --ssl-reqd -u alice:geheim -T report.pdf \
  ftp://files.example.org/projekte/report.pdf

# Download
curl --ssl-reqd -u alice:geheim \
  ftp://files.example.org/projekte/report.pdf -o report.pdf

# Download fortsetzen (Resume)
curl --ssl-reqd -u alice:geheim -C - \
  ftp://files.example.org/projekte/report.pdf -o report.pdf

# Listen
curl --ssl-reqd -u alice:geheim ftp://files.example.org/projekte/
```

### Python (`ftplib`)

```python
import ftplib, ssl

ctx = ssl.create_default_context()
ftp = ftplib.FTP_TLS(context=ctx)
try:
    ftp.connect("files.example.org", 21)
    ftp.login("alice", "geheim")
    ftp.prot_p()

    ftp.cwd("/projekte")
    print(ftp.nlst())                       # Listing

    with open("report.pdf", "wb") as fh:
        ftp.retrbinary("RETR report.pdf", fh.write)

    with open("neu.pdf", "rb") as fh:
        ftp.storbinary("STOR neu.pdf", fh)
finally:
    ftp.quit()
```

Resume: `ftp.retrbinary("RETR report.pdf", fh.write, rest=<offset>)` bzw.
`ftp.storbinary("STOR report.pdf", fh, rest=<offset>)`.

---

## 4. Rechte im FTP-Kontext

Der FTP-Login wird gegen den IFS-Benutzerbestand geprüft. Für jedes Kommando wird das
passende Recht verlangt:

| Kommando | Recht |
|---|---|
| `CWD`, `LIST`, `NLST`, `MLSD`, `RETR` | `read` |
| `MKD`, `STOR`, `APPE`, `RNTO` | `write` |
| `DELE`, `RMD` | `delete` |

Rechte werden entlang der Ordnerhierarchie vererbt. Ein Benutzer sieht nur Ordner, für
die er mindestens `read` besitzt. Details: [Sicherheit](sicherheit.md).

---

## 5. Zertifikate

- Pflicht: `IFS_FTP_CERTFILE` und `IFS_FTP_KEYFILE`.
- Entwicklung: `python scripts/gen-dev-cert.py certs`.
- Produktion: echtes Zertifikat (Let's Encrypt, interne CA). Bei Erneuerung den
  FTP-Dienst neu starten bzw. laden.
- Clients müssen dem Zertifikat vertrauen; sonst Verbindungsabbruch. In Dev-Clients kann
  die Prüfung vorübergehend deaktiviert werden.

---

## 6. Betriebliche Hinweise

- **Firewall/NAT:** Port 21 (TCP) und die Passive-Portrange öffnen.
- **Container:** Passive-Ports müssen im `docker-compose.yml` gemappt sein.
- **Masquerade (Docker/NAT):** `IFS_FTP_MASQUERADE_ADDRESS` auf die von außen erreichbare
  Adresse setzen. Ohne diese Angabe antwortet `PASV` mit der **Container-IP** (z. B.
  `172.18.0.5`); die Steuerverbindung klappt, der Datenkanal läuft aber ins Timeout
  (WinSCP: „Zeit abgelaufen (Datenverbindung)“). Nach Änderung den Container neu erstellen:
  `docker compose up -d ftp`.
- **Sitzungslimits:** pro Benutzer mehrere Sessions möglich; Verbindungs- und
  Datenverbindungs-Timeouts setzt pyftpdlib mit Standardwerten.
- **Audit:** Logins, CWDs und Transfers werden protokolliert (Audit-Log).

Weiter: [Tutorial 3 – FTPS](../tutorials/03-ftps-mit-filezilla-und-curl.md) ·
[Troubleshooting](../tutorials/10-troubleshooting.md)
