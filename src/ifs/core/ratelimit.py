"""Einfacher In-Memory-Rate-Limiter für Login-Versuche (Backoff/Lockout).

Wird von HTTP- und FTP-Login identisch genutzt. Pro Prozess; für den internen
Maßstab (eine API-/FTP-Instanz) ausreichend. Es gibt zwei Zähler:
- pro Konto (IP + Benutzername) mit strengerem Limit,
- pro IP mit höherem Limit (Schutz vor Credential-Spraying über viele Namen).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..config import get_settings


@dataclass
class _Entry:
    count: int = 0
    first_failure: float = 0.0
    locked_until: float = 0.0


class LoginRateLimiter:
    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        base_lock_seconds: int,
        max_lock_seconds: int,
        max_entries: int = 10000,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.window_seconds = max(1, window_seconds)
        self.base_lock_seconds = max(1, base_lock_seconds)
        self.max_lock_seconds = max(self.base_lock_seconds, max_lock_seconds)
        # Obergrenze gegen unbegrenztes Wachstum (Credential-Spraying mit vielen Keys).
        self.max_entries = max(1, max_entries)
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        if len(self._entries) <= self.max_entries:
            return
        stale = [
            key
            for key, entry in self._entries.items()
            if entry.locked_until <= now and now - entry.first_failure > self.window_seconds
        ]
        for key in stale:
            self._entries.pop(key, None)
        if len(self._entries) > self.max_entries:
            oldest = sorted(self._entries, key=lambda key: self._entries[key].first_failure)
            for key in oldest[: len(self._entries) - self.max_entries]:
                self._entries.pop(key, None)

    def _now(self) -> float:
        return time.monotonic()

    def retry_after(self, key: str) -> int:
        """Sekunden bis zum nächsten erlaubten Versuch (0 = erlaubt)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return 0
            now = self._now()
            if entry.locked_until > now:
                return int(entry.locked_until - now) + 1
            if now - entry.first_failure > self.window_seconds:
                self._entries.pop(key, None)
            return 0

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._now()
            entry = self._entries.get(key)
            if entry is None or now - entry.first_failure > self.window_seconds:
                entry = _Entry(first_failure=now)
                self._entries[key] = entry
            entry.count += 1
            if entry.count >= self.max_attempts:
                over = entry.count - self.max_attempts
                lock = min(self.base_lock_seconds * (2 ** over), self.max_lock_seconds)
                entry.locked_until = now + lock
            self._prune_locked(now)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


class FixedWindowLimiter:
    """Einfacher Zaehler pro festem Zeitfenster (fuer nicht-Login-Endpunkte).

    Anders als der Login-Limiter blockiert er nicht progressiv, sondern erlaubt
    ``max_events`` Aktionen je ``window_seconds`` und lehnt danach ab.
    """

    def __init__(self, max_events: int, window_seconds: int, max_entries: int = 10000) -> None:
        self.max_events = max(1, max_events)
        self.window_seconds = max(1, window_seconds)
        self.max_entries = max(1, max_entries)
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        if len(self._buckets) <= self.max_entries:
            return
        stale = [
            key
            for key, (start, _count) in self._buckets.items()
            if now - start >= self.window_seconds
        ]
        for key in stale:
            self._buckets.pop(key, None)
        if len(self._buckets) > self.max_entries:
            oldest = sorted(self._buckets, key=lambda key: self._buckets[key][0])
            for key in oldest[: len(self._buckets) - self.max_entries]:
                self._buckets.pop(key, None)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= self.window_seconds:
                start, count = now, 0
            if count >= self.max_events:
                self._buckets[key] = (start, count)
                self._prune_locked(now)
                return False
            self._buckets[key] = (start, count + 1)
            self._prune_locked(now)
            return True

    def retry_after(self, key: str) -> int:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0
            start, count = bucket
            if count < self.max_events:
                return 0
            remaining = start + self.window_seconds - time.monotonic()
            return max(1, int(remaining) + 1)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_account_limiter: LoginRateLimiter | None = None
_ip_limiter: LoginRateLimiter | None = None
_share_upload_limiter: FixedWindowLimiter | None = None
_published_download_limiter: FixedWindowLimiter | None = None
_init_lock = threading.Lock()


def _limiters() -> tuple[LoginRateLimiter, LoginRateLimiter]:
    global _account_limiter, _ip_limiter
    if _account_limiter is None or _ip_limiter is None:
        with _init_lock:
            if _account_limiter is None or _ip_limiter is None:
                settings = get_settings()
                _account_limiter = LoginRateLimiter(
                    settings.login_max_attempts,
                    settings.login_window_seconds,
                    settings.login_lock_seconds,
                    settings.login_max_lock_seconds,
                )
                _ip_limiter = LoginRateLimiter(
                    settings.login_ip_max_attempts,
                    settings.login_window_seconds,
                    settings.login_lock_seconds,
                    settings.login_max_lock_seconds,
                )
    return _account_limiter, _ip_limiter


def keys(ip: str | None, username: str | None) -> tuple[str, str]:
    """Liefert (Konto-Key, IP-Key)."""
    ip_part = ip or "unknown"
    return f"{ip_part}|{(username or '').lower()}", f"ip:{ip_part}"


def retry_after(account_key: str, ip_key: str) -> int:
    account, by_ip = _limiters()
    return max(account.retry_after(account_key), by_ip.retry_after(ip_key))


def record_failure(account_key: str, ip_key: str) -> None:
    account, by_ip = _limiters()
    account.record_failure(account_key)
    by_ip.record_failure(ip_key)


def record_success(account_key: str, ip_key: str | None = None) -> None:
    """Löscht nur den Konto-Zähler, nicht den IP-Zähler.

    Der IP-Zähler bremst Credential-Spraying über viele Benutzernamen und darf
    nicht durch einen erfolgreichen Login (ggf. mit gültigem Konto) zurückgesetzt
    werden. ``ip_key`` bleibt nur aus Kompatibilität erhalten und wird nicht genutzt.
    """
    account, _ = _limiters()
    account.record_success(account_key)


def _share_limiter() -> FixedWindowLimiter:
    global _share_upload_limiter
    if _share_upload_limiter is None:
        with _init_lock:
            if _share_upload_limiter is None:
                settings = get_settings()
                _share_upload_limiter = FixedWindowLimiter(
                    settings.share_upload_max_requests,
                    settings.share_upload_window_seconds,
                )
    return _share_upload_limiter


def allow_share_upload(key: str) -> bool:
    """Erlaubt/verbietet einen oeffentlichen Share-Upload fuer ``key``."""
    return _share_limiter().allow(key)


def share_upload_retry_after(key: str) -> int:
    return _share_limiter().retry_after(key)


def _published_download_limiter_instance() -> FixedWindowLimiter:
    global _published_download_limiter
    if _published_download_limiter is None:
        with _init_lock:
            if _published_download_limiter is None:
                settings = get_settings()
                _published_download_limiter = FixedWindowLimiter(
                    settings.published_download_max_requests,
                    settings.published_download_window_seconds,
                )
    return _published_download_limiter


def allow_published_download(key: str) -> bool:
    """Erlaubt/verbietet einen anonymen Download einer Veroeffentlichung fuer ``key``."""
    return _published_download_limiter_instance().allow(key)


def published_download_retry_after(key: str) -> int:
    return _published_download_limiter_instance().retry_after(key)


def reset() -> None:
    """Setzt alle Zähler und die Konfiguration zurück (für Tests)."""
    global _account_limiter, _ip_limiter, _share_upload_limiter, _published_download_limiter
    with _init_lock:
        _account_limiter = None
        _ip_limiter = None
        _share_upload_limiter = None
        _published_download_limiter = None


def install(account: LoginRateLimiter, by_ip: LoginRateLimiter) -> None:
    """Ersetzt die Limiter (für Tests)."""
    global _account_limiter, _ip_limiter
    with _init_lock:
        _account_limiter = account
        _ip_limiter = by_ip


def install_share_limiter(limiter: FixedWindowLimiter) -> None:
    """Ersetzt den Share-Upload-Limiter (für Tests)."""
    global _share_upload_limiter
    with _init_lock:
        _share_upload_limiter = limiter


def install_published_limiter(limiter: FixedWindowLimiter) -> None:
    """Ersetzt den Veroeffentlichungs-Download-Limiter (für Tests)."""
    global _published_download_limiter
    with _init_lock:
        _published_download_limiter = limiter
