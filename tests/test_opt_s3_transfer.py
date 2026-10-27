"""Tests für das S3-Multipart-Tuning beim Upload."""

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


def test_upload_file_uses_multipart_defaults(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeClient:
        def upload_file(self, filename, bucket, key, ExtraArgs=None, Config=None):
            captured.update(
                filename=filename, bucket=bucket, key=key, extra=ExtraArgs, config=Config
            )

    monkeypatch.setattr(content, "get_settings", lambda: _settings())
    monkeypatch.setattr(content, "get_s3_client", lambda: FakeClient())

    path = tmp_path / "blob.bin"
    path.write_bytes(b"x" * 4096)
    sha = "ab" * 32
    info = content.upload_file(str(path), sha)

    assert info.storage_key == f"cas/ab/{sha}"
    assert info.sha256 == sha
    assert info.size == 4096
    assert captured["bucket"] == "ifs"
    assert captured["key"] == f"cas/ab/{sha}"
    config = captured["config"]
    assert config.multipart_threshold == 8 * 1024 * 1024
    assert config.multipart_chunksize == 16 * 1024 * 1024
    assert config.max_concurrency == 8


def test_upload_file_multipart_is_configurable(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeClient:
        def upload_file(self, filename, bucket, key, ExtraArgs=None, Config=None):
            captured["config"] = Config

    monkeypatch.setattr(
        content,
        "get_settings",
        lambda: _settings(
            s3_multipart_threshold_mb=4,
            s3_multipart_chunksize_mb=32,
            s3_transfer_concurrency=4,
        ),
    )
    monkeypatch.setattr(content, "get_s3_client", lambda: FakeClient())

    path = tmp_path / "blob.bin"
    path.write_bytes(b"x")
    content.upload_file(str(path), "cd" * 32)

    config = captured["config"]
    assert config.multipart_threshold == 4 * 1024 * 1024
    assert config.multipart_chunksize == 32 * 1024 * 1024
    assert config.max_concurrency == 4
