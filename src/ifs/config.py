"""Zentrale Konfiguration (pydantic-settings, Präfix IFS_)."""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .utils import parse_port_range

logger = logging.getLogger("ifs.config")

WEAK_JWT_SECRETS = {"", "change-me", "secret", "test-secret"}
# Nur HMAC-Verfahren zulassen – verhindert Fehlkonfigurationen wie `none` oder
# asymmetrische Algorithmen mit unbekanntem Schlüsselmaterial.
ALLOWED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}
WEAK_S3_SECRETS = {"", "minioadmin", "test", "change-me"}
# Default-Zugangsdaten, die in Produktion nicht verwendet werden dürfen.
WEAK_DB_CREDENTIAL_MARKERS = (
    "ifs:ifs@",
    "postgres:postgres@",
    "root:root@",
    "admin:admin@",
)
MIN_JWT_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 8
PRODUCTION_ENV_NAMES = {"prod", "production"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IFS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "IFS"
    environment: str = "dev"
    log_level: str = "INFO"

    # Datenbank
    database_url: str = "postgresql+psycopg://ifs:ifs@localhost:5432/ifs"
    auto_create_schema: bool = False

    # Auth
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    login_max_attempts: int = 5
    login_ip_max_attempts: int = 20
    login_window_seconds: int = 300
    login_lock_seconds: int = 30
    login_max_lock_seconds: int = 900
    # Nur wenn der direkte Peer in dieser Liste (CIDR, kommagetrennt) liegt, wird
    # X-Forwarded-For für die Client-IP ausgewertet.
    trusted_proxies: str = ""

    # Informations-Endpunkte
    expose_docs: bool = False
    metrics_public: bool = False

    # S3
    s3_endpoint_url: str | None = None
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "ifs"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    s3_sse: str | None = "AES256"
    s3_presign_ttl_seconds: int = 300
    cas_dedup: bool = True

    # Uploads
    upload_spool_dir: str = "/var/lib/ifs/spool"
    max_upload_size: int = 0  # 0 = unbegrenzt (Bytes)
    # Hartes Groessenlimit fuer *oeffentliche* Share-Uploads. Greift auch dann,
    # wenn IFS_MAX_UPLOAD_SIZE=0 (unbegrenzt) gesetzt ist. 0 = unbegrenzt.
    share_upload_max_size: int = 100 * 1024 * 1024
    # Rate-Limit fuer oeffentliche Share-Uploads (pro Freigabe+IP).
    share_upload_max_requests: int = 60
    share_upload_window_seconds: int = 300
    default_quota_bytes: int = 0  # 0 = unbegrenzt (Gesamt-Quota pro Besitzer)
    trash_retention_days: int = 30

    # Worker: Intervall zwischen den Läufen (Sekunden) und Batch-Größe der Outbox.
    worker_interval_seconds: int = 10
    worker_outbox_batch_size: int = 200

    # Monitoring: Pfad für die Festplattenanzeige im Admin-Panel
    monitor_disk_path: str = "/"
    # Optionaler SeaweedFS-Status-Endpunkt (z. B. http://s3:9333/vol/status)
    seaweedfs_status_url: str | None = None

    # FTPS
    ftp_enabled: bool = True
    ftp_host: str = "0.0.0.0"
    ftp_port: int = 21
    ftp_passive_ports: str = "30000-30100"
    ftp_masquerade_address: str | None = None
    ftp_certfile: str | None = None
    ftp_keyfile: str | None = None
    ftp_banner: str = "IFS FTPS ready"

    # Admin-Seed (nur wenn ein Passwort gesetzt ist)
    admin_username: str = "admin"
    admin_password: str | None = None

    @model_validator(mode="after")
    def _enforce_security(self) -> Settings:
        production = (self.environment or "dev").strip().lower() in PRODUCTION_ENV_NAMES

        if (
            self.jwt_secret in WEAK_JWT_SECRETS
            or len(self.jwt_secret) < MIN_JWT_SECRET_LENGTH
        ):
            if production:
                raise ValueError(
                    "IFS_JWT_SECRET muss gesetzt und mindestens "
                    f"{MIN_JWT_SECRET_LENGTH} Zeichen lang sein "
                    "(z. B. `openssl rand -hex 32`)."
                )
            self.jwt_secret = secrets.token_urlsafe(48)
            logger.warning(
                "Kein ausreichend starkes IFS_JWT_SECRET gesetzt – es wurde ein zufälliges "
                "erzeugt. Tokens werden bei jedem Neustart ungültig. Bitte in .env setzen."
            )

        if self.jwt_algorithm not in ALLOWED_JWT_ALGORITHMS:
            if production:
                raise ValueError(
                    "IFS_JWT_ALGORITHM muss HS256, HS384 oder HS512 sein."
                )
            logger.warning(
                "Ungültiges IFS_JWT_ALGORITHM %r – es wird HS256 verwendet.",
                self.jwt_algorithm,
            )
            self.jwt_algorithm = "HS256"

        if production:
            if self.admin_password is not None and (
                len(self.admin_password) < MIN_PASSWORD_LENGTH
                or self.admin_password == "admin"
            ):
                raise ValueError(
                    "IFS_ADMIN_PASSWORD ist zu schwach (mind. "
                    f"{MIN_PASSWORD_LENGTH} Zeichen, nicht 'admin')."
                )
            if self.s3_secret_key in WEAK_S3_SECRETS:
                raise ValueError(
                    "IFS_S3_SECRET_KEY ist unsicher gesetzt – bitte einen eigenen Wert verwenden."
                )
            if any(
                marker in (self.database_url or "")
                for marker in WEAK_DB_CREDENTIAL_MARKERS
            ):
                raise ValueError(
                    "IFS_DATABASE_URL verwendet Default-Zugangsdaten – in Produktion bitte "
                    "ein eigenes Datenbank-Passwort setzen."
                )

        return self

    @property
    def is_production(self) -> bool:
        return (self.environment or "dev").strip().lower() in PRODUCTION_ENV_NAMES

    @property
    def passive_port_range(self) -> range:
        return parse_port_range(self.ftp_passive_ports)

    @property
    def max_upload_size_or_none(self) -> int | None:
        return self.max_upload_size or None

    @property
    def effective_share_upload_limit(self) -> int:
        """Effektives Groessenlimit fuer oeffentliche Share-Uploads.

        Es gilt das kleinere der beiden positiven Limits. Sind beide 0, bleibt der
        Upload unbegrenzt (bewusste Opt-out-Entscheidung des Betreibers).
        """
        limits = [
            value
            for value in (self.max_upload_size, self.share_upload_max_size)
            if value and value > 0
        ]
        return min(limits) if limits else 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
