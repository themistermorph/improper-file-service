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
| `DELE` | Soft-Delete der Datei (Papierkorb) |
| `RMD` | Soft-Delete des Ordners **samt Inhalt** (rekursiv, wie `DELETE /api/entries/{id}`) |
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

### FTP-WinMount (Windows-Laufwerk)

[FTP-WinMount](https://github.com/dansasser/ftp-winmount) bindet den FTPS-Dienst als
Laufwerksbuchstaben ein. FTPS muss **explizit** aktiviert werden; ohne `--secure`
verbindet das Tool im Klartext und IFS lehnt den Login mit
„550 SSL/TLS required on the control channel“ ab.

```powershell
ftp-winmount mount --host files.example.org --port 21 `
  --user alice --password geheim --drive Z --secure
```

Alternativ in der Konfigurationsdatei im Abschnitt `[ftp]` `secure = true` setzen
(und `passive_mode = true`).

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

---

## 7. Durchsatz und Tuning

Der FTPS-Datenpfad besteht aus drei Abschnitten, die jeweils eigene Stellschrauben
haben:

```
Client ⇄ (TLS) ⇄ Datenkanal-Puffer ⇄ IFS-Gateway ⇄ S3-Connection-Pool ⇄ Objektspeicher
```

### 7.1 Download: S3-Read-Ahead

pyftpdlib liest Dateien blockweise (Standard 64 KiB). Würde für jeden Block ein
eigener S3-Range-Request geöffnet, entstünde **ein voller, signierter S3-Round-Trip
pro Block** – der Durchsatz bricht dann auf WAN-Strecken ein.

Das Gateway öffnet deshalb **einen zusammenhängenden Range** mit
`IFS_FTP_S3_READAHEAD_BYTES` Bytes (Standard 4 MiB) und liest ihn als fortlaufenden
Stream. Erst am Bereichsende oder nach einem `REST`/Seek ist ein neuer Request
nötig. Beispiel: Für eine 1-GiB-Datei sinkt die Zahl der S3-Requests von ~16 384
(64 KiB) auf ~256 (4 MiB). `REST`-Resume und Teil-Downloads funktionieren
unverändert, weil bei einem Seek neu geöffnet wird.

### 7.2 Datenkanal: Blockgröße

`IFS_FTP_TRANSFER_BUFFER_BYTES` (Standard 256 KiB) setzt sowohl die Socket-Puffer
(`ac_in_buffer_size`/`ac_out_buffer_size`) als auch die Blockgröße des
Datei-Producers. Größere Blöcke bedeuten weniger `recv`/`send`-Syscalls und weniger
TLS-Records je Transfer. Die Untergrenze liegt bei 16 KiB; darunter dominiert der
Syscall-Overhead. Pro aktivem Transfer wird der Puffer einmal belegt.

### 7.3 Parallele Transfers: S3-Connection-Pool

`IFS_S3_MAX_POOL_CONNECTIONS` (Standard 32) hebt das botocore-Limit von 10 an.
Ohne Anpassung konkurrieren mehrere gleichzeitige FTPS-/HTTP-Transfers um zu wenige
Verbindungen und serialisieren sich; das bremst vor allem den **aggregierten**
Durchsatz bei vielen Nutzern.

### 7.4 Uploads

Uploads werden lokal gespoolt und erst nach dem Transfer nach S3 geschrieben. Das
Größenlimit wird über einen Byte-Zähler geprüft, nicht per `fstat` je Block. Für den
Upload-Durchsatz gelten dieselben Empfehlungen wie in 7.2.

### 7.5 Richtwerte

| Umgebung | `IFS_FTP_TRANSFER_BUFFER_BYTES` | `IFS_FTP_S3_READAHEAD_BYTES` | `IFS_S3_MAX_POOL_CONNECTIONS` |
|---|---|---|---|
| LAN, wenige Nutzer | `262144` (Standard) | `4194304` (Standard) | `32` |
| WAN, viele parallele Clients | `262144`–`1048576` | `8388608`–`16777216` | `64`+ |
| Speicherbegrenzter Container | `65536` | `1048576` | `32` |

> **Hinweis:** Ein größerer Read-Ahead-Bereich belegt nicht automatisch
> entsprechend viel Heap – gelesen wird blockweise. Er bindet aber länger eine
> S3-Verbindung aus dem Pool. Bei sehr vielen gleichzeitigen, langsamen Clients
> daher den Pool (`IFS_S3_MAX_POOL_CONNECTIONS`) entsprechend groß wählen.

### 7.6 Messen

- **Client-seitig:** `curl --ssl-reqd -u … -T datei ftp://…` bzw. `-o` für den
  Download; `lftp` zeigt die Rate nach dem Transfer an.
- **Server-seitig:** Die Transfer-Logs (`ifs.ftp`, Audit-Log) enthalten Dauer und
  Bytezahl je Transfer. Die Rate ist `bytes / elapsed`.
- **Vergleich:** Vor dem Ändern der Werte dieselbe Datei und denselben Client
  verwenden; TLS-Handshake und Signatur betreffen nur den Rumpf, nicht die Rate.

Weiter: [Tutorial 3 – FTPS](../tutorials/03-ftps-mit-filezilla-und-curl.md) ·
[Troubleshooting](../tutorials/10-troubleshooting.md)
