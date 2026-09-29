"""Erzeugt ein selbstsigniertes Entwicklungszertifikat für FTPS.

Aufruf:
    python scripts/gen-dev-cert.py [zielverzeichnis]

Standardziel: ./certs  (dort erwartet docker-compose die Dateien ftps.crt/ftps.key)
"""

from __future__ import annotations

import datetime
import pathlib
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate(target: pathlib.Path, common_name: str = "localhost") -> None:
    target.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(common_name), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (target / "ftps.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    # Privaten Schlüssel auf 0600 einschränken (Best-Effort; unter Windows nur
    # eingeschränkt wirksam, daher Fehler bewusst ignorieren).
    try:
        (target / "ftps.key").chmod(0o600)
    except OSError:
        pass
    (target / "ftps.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Zertifikat erzeugt: {target / 'ftps.crt'} / {target / 'ftps.key'}")


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "certs")
    name = sys.argv[2] if len(sys.argv) > 2 else "localhost"
    generate(out, name)
