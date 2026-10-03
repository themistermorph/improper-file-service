"""System-Metriken für das Admin-Panel (Linux via /proc, sonst graceful Fallback).

Netzwerk- und CPU-Raten werden aus der Differenz zweier Aufrufe berechnet (Sampler);
der erste Aufruf liefert daher keine Rate.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Entry, EntryType, User


@dataclass
class _Sample:
    time: float | None = None
    cpu_total: int | None = None
    cpu_idle: int | None = None
    net_rx: int | None = None
    net_tx: int | None = None


_sample = _Sample()
_lock = threading.Lock()

# Kurzer TTL-Cache für die DB-Kennzahlen (Benutzer/Einträge/Speicher): schnelles
# Polling mehrerer Admin-Clients erzeugt so höchstens eine Abfrage pro Fenster.
_DB_METRICS_TTL = 1.0  # Sekunden
_db_metrics_lock = threading.Lock()
# (Bind, Zeitstempel, Werte). Der Bind wird absichtlich stark referenziert, damit
# die Identitätsprüfung nach einem Engine-Wechsel (z. B. in Tests) eindeutig bleibt.
_db_metrics_cache: tuple[object, float, dict] | None = None


def reset_db_metrics_cache() -> None:
    """Leert den TTL-Cache der DB-Kennzahlen (Tests, Engine-Wechsel)."""
    global _db_metrics_cache
    with _db_metrics_lock:
        _db_metrics_cache = None


def ifs_metrics(db: Session, *, use_cache: bool = True) -> dict:
    """Zählt Benutzer/Einträge und summiert Dateigrößen in *einer* Abfrage.

    Die drei Kennzahlen werden als skalare Subqueries in einem Statement
    ermittelt (statt drei Einzelabfragen). Optional liefert ein kurzer TTL-Cache
    das zuletzt ermittelte Ergebnis, ohne die Datenbank erneut zu belasten.
    """
    global _db_metrics_cache
    now = time.monotonic()
    bind = db.get_bind()
    if use_cache:
        with _db_metrics_lock:
            cached = _db_metrics_cache
            if cached is not None and cached[0] is bind and now - cached[1] < _DB_METRICS_TTL:
                return dict(cached[2])

    users, entries, stored_bytes = db.execute(
        select(
            select(func.count()).select_from(User).scalar_subquery(),
            select(func.count()).select_from(Entry).scalar_subquery(),
            select(func.coalesce(func.sum(Entry.size), 0))
            .where(Entry.type == EntryType.file)
            .scalar_subquery(),
        )
    ).one()
    values = {
        "users": int(users),
        "entries": int(entries),
        "stored_bytes": int(stored_bytes or 0),
    }
    if use_cache:
        with _db_metrics_lock:
            _db_metrics_cache = (bind, now, values)
    return dict(values)


def _read_cpu_times() -> tuple[int, int] | None:
    try:
        with open("/proc/stat") as handle:
            parts = handle.readline().split()
            if not parts or parts[0] != "cpu":
                return None
            values = [int(value) for value in parts[1:11]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return sum(values), idle
    except (OSError, ValueError, IndexError):
        return None


def _read_memory() -> dict | None:
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.strip().split()[0]) * 1024
        total = info.get("MemTotal")
        available = info.get("MemAvailable") or info.get("MemFree")
        if not total or available is None:
            return None
        used = total - available
        return {
            "total": total,
            "available": available,
            "used": used,
            "percent": round(used / total * 100, 1),
        }
    except (OSError, ValueError):
        return None


def _read_loadavg() -> tuple[float, float, float] | None:
    try:
        with open("/proc/loadavg") as handle:
            parts = handle.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return None


def _read_uptime() -> float | None:
    try:
        with open("/proc/uptime") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _read_net_totals() -> tuple[int, int] | None:
    try:
        rx = tx = 0
        with open("/proc/net/dev") as handle:
            for line in handle.readlines()[2:]:
                if ":" not in line:
                    continue
                _, data = line.split(":", 1)
                fields = data.split()
                rx += int(fields[0])
                tx += int(fields[8])
        return rx, tx
    except (OSError, ValueError, IndexError):
        return None


def _disk_usage(path: str) -> dict | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    percent = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
    return {
        "path": path,
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": percent,
    }


def _seaweedfs(status_url: str | None) -> dict | None:
    """Liest Volume-Status von SeaweedFS (Dateianzahl/Belegung) über HTTP."""
    if not status_url:
        return None
    # Nur HTTP(S) zulassen – verhindert SSRF über file:// oder ähnliche Schemata.
    if urllib.parse.urlsplit(status_url).scheme not in ("http", "https"):
        return None
    try:
        with urllib.request.urlopen(status_url, timeout=3) as response:
            data = json.load(response)
    except Exception:
        return None

    totals = {"volumes": 0, "files": 0, "used_bytes": 0}

    def walk(node) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and "Id" in item:
                    totals["volumes"] += 1
                    totals["used_bytes"] += int(item.get("Size") or 0)
                    totals["files"] += int(item.get("FileCount") or 0)
                else:
                    walk(item)

    try:
        walk(data.get("Volumes", data) if isinstance(data, dict) else data)
    except (TypeError, ValueError):
        # Unerwartetes Antwortformat des Status-Endpunkts – Monitoring bleibt stabil.
        return None
    return totals


def collect(db: Session, disk_path: str = "/", seaweedfs_status_url: str | None = None) -> dict:
    now = time.monotonic()
    cpu = _read_cpu_times()
    memory = _read_memory()
    load = _read_loadavg()
    uptime = _read_uptime()
    disk = _disk_usage(disk_path)
    net = _read_net_totals()

    cpu_percent: float | None = None
    rx_rate: float | None = None
    tx_rate: float | None = None

    with _lock:
        if cpu and _sample.cpu_total is not None and _sample.time is not None:
            total_delta = cpu[0] - _sample.cpu_total
            idle_delta = cpu[1] - _sample.cpu_idle
            if total_delta > 0:
                cpu_percent = round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)
        if net and _sample.net_rx is not None and _sample.time is not None:
            elapsed = now - _sample.time
            if elapsed > 0:
                rx_rate = max(0, net[0] - _sample.net_rx) / elapsed
                tx_rate = max(0, net[1] - _sample.net_tx) / elapsed
        _sample.time = now
        if cpu:
            _sample.cpu_total, _sample.cpu_idle = cpu
        if net:
            _sample.net_rx, _sample.net_tx = net

    ifs = ifs_metrics(db)

    return {
        "cpu": {
            "cores": os.cpu_count(),
            "percent": cpu_percent,
            "load_1": load[0] if load else None,
            "load_5": load[1] if load else None,
            "load_15": load[2] if load else None,
        },
        "memory": memory,
        "disk": disk,
        "network": {
            "rx_bytes_per_sec": rx_rate,
            "tx_bytes_per_sec": tx_rate,
            "rx_total": net[0] if net else None,
            "tx_total": net[1] if net else None,
        },
        "uptime_seconds": uptime,
        "ifs": ifs,
        "seaweedfs": _seaweedfs(seaweedfs_status_url),
    }
