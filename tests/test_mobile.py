"""Tests für die mobile Oberfläche unter /mobile."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ifs import main

MOBILE = Path(__file__).resolve().parents[1] / "src/ifs/web/mobile.html"


def _page() -> str:
    return MOBILE.read_text(encoding="utf-8")


def test_mobile_seite_wird_ausgeliefert():
    with TestClient(main.app) as client:
        for path in ("/mobile", "/mobile/"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "IFS – Mobil" in response.text
            assert 'name="viewport"' in response.text
            assert 'id="app"' in response.text


def test_mobile_seite_nutzt_dieselben_api_endpunkte():
    page = _page()
    for endpoint in (
        "/api/auth/login",
        "/api/me",
        "/api/root",
        "/api/entries",
        "/api/shared",
        "/api/shared/outgoing",
        "/api/shares",
        "/api/published/mine",
        "/api/folders",
        "/api/uploads/simple",
        "/download-token",
    ):
        assert endpoint in page, f"Mobile Seite nutzt {endpoint} nicht"
    for function in (
        "renderGeteilt", "renderShares", "renderPublished",
        "openShareSheet", "openPublishedSheet", "refreshMarkers",
    ):
        assert f"function {function}" in page

    # Geteilt-/Veröffentlicht-Hinweise im mobilen Dateimanager.
    assert "state.sharedIds.has(entry.id)" in page
    assert "state.publishedIds.has(entry.id)" in page
    assert "🤝 geteilt" in page
    assert "🌐 veröffentlicht" in page


def test_mobile_navigation_enthaelt_geteilt_freigaben_und_veroeffentlichungen():
    page = _page()
    for element_id in ("tab-geteilt", "tab-shares", "tab-published"):
        assert f'id="{element_id}"' in page
    for label in ("Geteilt", "Freigaben", "Meine Veröffentlichungen"):
        assert label in page
    # „Mit mir geteilt“ bleibt über das Konto-Menü erreichbar.
    assert "Mit mir geteilt" in page


def test_mobile_seite_ist_offline_faehig_und_touch_freundlich():
    page = _page()
    # Keine externen Skripte/Styles: die Seite bleibt eine einzelne Datei.
    assert "http://" not in page
    assert "https://" not in page
    assert "<script src=" not in page
    assert "<link rel=\"stylesheet\"" not in page
    # Touch-Ziele und sichere Bereiche.
    assert "min-height: 44px" in page or "min-height: 48px" in page
    assert "env(safe-area-inset-bottom)" in page
    # Navigation und Kernaktionen vorhanden.
    for element_id in ("tab-files", "tab-geteilt", "file-input", "login-form", "search"):
        assert f'id="{element_id}"' in page


def test_desktop_menu_verlinkt_mobile_ansicht():
    index = (
        Path(__file__).resolve().parents[1] / "src/ifs/web/index.html"
    ).read_text(encoding="utf-8")
    assert "window.location.href='/mobile'" in index
