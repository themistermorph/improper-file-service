"""Prüft relative Markdown-Links in README, docs/ und tutorials/ auf Existenz.

Aufruf aus dem Projektwurzelverzeichnis:

    python scripts/check-doc-links.py
"""

from __future__ import annotations

import pathlib
import re
import sys

LINK = re.compile(r"\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "#", "mailto:")


def main() -> int:
    root = pathlib.Path.cwd()
    files = (
        list(root.glob("*.md"))
        + list((root / "docs").glob("*.md"))
        + list((root / "tutorials").glob("*.md"))
    )
    missing: list[tuple[pathlib.Path, str]] = []
    for file in files:
        for target in LINK.findall(file.read_text(encoding="utf-8")):
            if target.startswith(SKIP_PREFIXES):
                continue
            path = target.split("#", 1)[0].strip()
            if not path:
                continue
            if not (file.parent / path).resolve().exists():
                missing.append((file, target))

    print(f"Geprüfte Markdown-Dateien: {len(files)}")
    for file, target in missing:
        print(f"FEHLT: {file} -> {target}")
    print(f"Tote Links: {len(missing)}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
