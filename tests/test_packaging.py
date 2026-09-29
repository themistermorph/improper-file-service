"""Stellt sicher, dass requirements.txt und pyproject.toml synchron bleiben."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _runtime_requirements() -> list[str]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def test_requirements_match_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = list(data["project"]["dependencies"])
    # Der Docker-Build installiert zuerst requirements.txt (Layer-Cache) und danach
    # das Paket ohne Abhängigkeiten. Beide Listen müssen identisch sein.
    assert sorted(_runtime_requirements()) == sorted(deps)
