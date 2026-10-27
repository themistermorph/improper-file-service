"""Statische Prüfungen des Datumsformats in der Web-UI.

Die Oberfläche zeigt Datum und Uhrzeit durchgängig als ``TT.MM.JJJJ, HH:MM:SS``.
Insbesondere darf kein ``<input type="date">`` verwendet werden, weil Browser
dieses Feld im Locale des Clients (z. B. ``MM/DD/YYYY``) darstellen.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src/ifs/web/index.html"
PUBLISHED = ROOT / "src/ifs/web/published.html"


def _read(path: Path) -> str:
    assert path.is_file(), f"Web-UI nicht gefunden: {path}"
    return path.read_text(encoding="utf-8")


def test_displayed_dates_use_day_month_year():
    for path in (INDEX, PUBLISHED):
        page = _read(path)
        assert "toLocaleString()" not in page
        assert "toLocaleDateString()" not in page
        assert "${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}" in page
        assert "${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}" in page


def test_share_expiry_uses_text_input_with_german_format():
    page = _read(INDEX)
    assert 'type="date"' not in page
    assert 'placeholder="TT.MM.JJJJ"' in page
    assert 'id="se-expires" type="text"' in page
    assert "function fmtDay(" in page
    assert "function parseDayInput(" in page
    assert "Bitte das Ablaufdatum als TT.MM.JJJJ eingeben." in page


def test_share_expiry_parser_rejects_invalid_values():
    page = _read(INDEX)
    # Parser prüft Format und Kalenderdatum, bevor ein ISO-Wert entsteht.
    assert r"/^(\d{2})\.(\d{2})\.(\d{4})$/" in page
    assert "date.getFullYear() !== year" in page
    assert "date.getMonth() !== month - 1" in page
    assert "date.getDate() !== day" in page
