"""Tests für das S3-Multipart-Tuning und den direkten Kleindatei-PUT."""

from __future__ import annotations

from types import SimpleNamespace

from ifs.core import content


def _settings(**overrides):
    base = {
        "s3_bucket": "ifs",
        "s3_sse": None,
        "s3_multipart_threshold_mb": 8,
        "s3_multipart_chunksize_mb": 16,
        "s3_transfer_concurrency": 8,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeClient:
    def __init__(self) -> None:
        self.puts: list[dict] = []
        self.uploads: list[dict] = []

    def put_object(self, **kwargs):
        payload = kwargs.pop("Body")
        self.puts.append({**kwargs, "body": payload.read()})

    def upload_file(self, filename, bucket, key, ExtraArgs=None, Config=None):
        self.uploads.append(
            {
                "filename": filename,
                "bucket": bucket,
                "key": key,
                "extra": ExtraArgs,
                "config": Config,
            }
        )


def test_upload_file_small_uses_direct_put(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(content, "get_settings", lambda: _settings())
    monkeypatch.setattr(content, "get_s3_client", lambda: client)

    path = tmp_path / "blob.bin"
    path.write_bytes(b"x" * 4096)
    sha = "ab" * 32
    info = content.upload_file(str(path), sha)

    assert info.storage_key == f"cas/ab/{sha}"
    assert info.sha256 == sha
    assert info.size == 4096
    assert client.uploads == []
    assert len(client.puts) == 1
    put = client.puts[0]
    assert put["Bucket"] == "ifs"
    assert put["Key"] == f"cas/ab/{sha}"
    assert put["ContentLength"] == 4096
    assert put["body"] == b"x" * 4096


def test_upload_file_small_passes_sse_and_mime(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(
        content, "get_settings", lambda: _settings(s3_sse="AES256")
    )
    monkeypatch.setattr(content, "get_s3_client", lambda: client)

    path = tmp_path / "blob.txt"
    path.write_bytes(b"hello")
    content.upload_file(str(path), "cd" * 32, mime="text/plain")

    put = client.puts[0]
    assert put["ServerSideEncryption"] == "AES256"
    assert put["ContentType"] == "text/plain"


def test_upload_file_large_uses_transfer_config(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(
        content,
        "get_settings",
        lambda: _settings(
            s3_multipart_threshold_mb=1,
            s3_multipart_chunksize_mb=32,
            s3_transfer_concurrency=4,
        ),
    )
    monkeypatch.setattr(content, "get_s3_client", lambda: client)

    path = tmp_path / "blob.bin"
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    content.upload_file(str(path), "ef" * 32)

    assert client.puts == []
    assert len(client.uploads) == 1
    upload = client.uploads[0]
    assert upload["bucket"] == "ifs"
    assert upload["key"] == f"cas/ef/{'ef' * 32}"
    config = upload["config"]
    assert config.multipart_threshold == 1 * 1024 * 1024
    assert config.multipart_chunksize == 32 * 1024 * 1024
    assert config.max_concurrency == 4
