"""Integrationstest des FTPS-Gateways (echte TLS-Verbindung, gemocktes S3)."""

from __future__ import annotations

import datetime
import io
import ssl
import threading

import pytest

pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from ifs.config import get_settings
from ifs.core import content, namespace
from ifs.db import session_scope
from ifs.models import Blob, BlobStatus, User
from ifs.security import hash_password


def _write_self_signed(cert_path, key_path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _fake_store_blob(db, local_path, mime=None):
    import hashlib

    data = open(local_path, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    blob = Blob(
        storage_key=f"cas/{sha[:2]}/{sha}",
        sha256=sha,
        size=len(data),
        status=BlobStatus.ready,
    )
    db.add(blob)
    db.flush()
    return blob


def _fake_get_object(key, start=None, end=None):
    payload = b"ftp content"
    if start is not None or end is not None:
        begin = start or 0
        finish = end if end is not None else len(payload) - 1
        payload = payload[begin : finish + 1]
    return {"Body": io.BytesIO(payload), "ContentLength": len(payload)}


def test_ftps_end_to_end(monkeypatch, tmp_path):
    from pyftpdlib.servers import ThreadedFTPServer

    from ifs.ftp.server import build_handler

    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with session_scope() as db:
        user = User(username="ftptester", password_hash=hash_password("secret"))
        db.add(user)
        db.flush()
        root = namespace.ensure_root(db, user.id)
        docs = namespace.create_folder(db, root, "docs", user.id)
        blob = Blob(
            storage_key="cas/ab/" + "ab" * 32,
            sha256="ab" * 32,
            size=len(b"ftp content"),
            status=BlobStatus.ready,
        )
        db.add(blob)
        db.flush()
        namespace.apply_write(db, docs, "file.txt", user.id, blob, "text/plain")

    cert = tmp_path / "ftps.crt"
    key = tmp_path / "ftps.key"
    _write_self_signed(cert, key)

    import os

    os.environ["IFS_FTP_CERTFILE"] = str(cert)
    os.environ["IFS_FTP_KEYFILE"] = str(key)
    os.environ["IFS_FTP_PASSIVE_PORTS"] = "41000-41010"
    get_settings.cache_clear()

    handler = build_handler()
    server = ThreadedFTPServer(("127.0.0.1", 0), handler)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import ftplib

    context = ssl._create_unverified_context()
    ftp = ftplib.FTP_TLS(context=context)
    try:
        ftp.connect("127.0.0.1", port, timeout=10)
        ftp.login("ftptester", "secret")
        ftp.prot_p()

        assert ftp.pwd() == "/"
        assert sorted(ftp.nlst()) == ["docs"]
        ftp.cwd("/docs")
        assert sorted(ftp.nlst()) == ["file.txt"]

        received = bytearray()
        ftp.retrbinary("RETR file.txt", received.extend)
        assert bytes(received) == b"ftp content"

        ftp.storbinary("STOR uploaded.bin", io.BytesIO(b"uploaded via ftps"))
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
        server.close_all()
        thread.join(timeout=5)

    with session_scope() as db:
        stored = namespace.resolve_path(db, "/docs/uploaded.bin", root)
        assert stored is not None
        assert stored.size == len(b"uploaded via ftps")


def test_ftp_enforces_max_upload_size(monkeypatch, tmp_path):
    """IFS_MAX_UPLOAD_SIZE gilt auch für FTP-Uploads (kein Spool-/Disk-Bypass)."""
    from pyftpdlib.servers import ThreadedFTPServer

    from ifs.ftp.server import build_handler

    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with session_scope() as db:
        user = User(username="ftp_limit", password_hash=hash_password("secret"))
        db.add(user)
        db.flush()
        root = namespace.ensure_root(db, user.id)

    cert = tmp_path / "limit.crt"
    key = tmp_path / "limit.key"
    _write_self_signed(cert, key)

    monkeypatch.setenv("IFS_FTP_CERTFILE", str(cert))
    monkeypatch.setenv("IFS_FTP_KEYFILE", str(key))
    monkeypatch.setenv("IFS_FTP_PASSIVE_PORTS", "42100-42110")
    monkeypatch.setenv("IFS_MAX_UPLOAD_SIZE", "10")
    get_settings.cache_clear()

    handler = build_handler()
    server = ThreadedFTPServer(("127.0.0.1", 0), handler)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import ftplib

    ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
    try:
        ftp.connect("127.0.0.1", port, timeout=10)
        ftp.login("ftp_limit", "secret")
        ftp.prot_p()
        try:
            ftp.storbinary("STOR big.bin", io.BytesIO(b"A" * 500))
        except Exception:
            pass  # Transfer darf/muss scheitern; entscheidend ist der Zustand unten.
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
        server.close_all()
        thread.join(timeout=5)
        get_settings.cache_clear()

    with session_scope() as db:
        assert namespace.resolve_path(db, "/big.bin", root) is None


def test_ftp_has_perm_denies_deactivated_user():
    from ifs.ftp.server import IFSAuthorizer

    with session_scope() as db:
        user = User(username="ftp_deactivated", password_hash=hash_password("secret"))
        db.add(user)
        db.flush()
        namespace.ensure_root(db, user.id)
        user_id = user.id

    authorizer = IFSAuthorizer()
    assert authorizer.has_perm("ftp_deactivated", "r", "/") is True

    with session_scope() as db:
        db.get(User, user_id).is_active = False

    assert authorizer.has_perm("ftp_deactivated", "r", "/") is False
    assert authorizer.has_perm("ftp_deactivated", "w", "/") is False
