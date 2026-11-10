"""Favicon-/Apple-Touch-Icon-Anfragen liefern einen leeren 204."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main


def test_favicon_und_apple_varianten_sind_leere_204():
    with TestClient(main.app) as client:
        for path in (
            "/favicon.ico",
            "/apple-touch-icon.png",
            "/apple-touch-icon-precomposed.png",
            "/apple-touch-icon-57x57.png",
            "/apple-touch-icon-120x120-precomposed.png",
            "/apple-touch-icon-180x180.png",
        ):
            response = client.get(path)
            assert response.status_code == 204, path
            assert response.content == b"", path


def test_favicon_head_ist_ebenfalls_leer():
    with TestClient(main.app) as client:
        for path in ("/favicon.ico", "/apple-touch-icon.png"):
            response = client.head(path)
            assert response.status_code == 204, path
            assert response.content == b"", path


def test_apple_pfade_ohne_png_bleiben_404():
    with TestClient(main.app) as client:
        assert client.get("/apple-touch-icon").status_code == 404
        assert client.get("/apple-touch-icon-not-an-icon.txt").status_code == 404
        assert client.get("/favicon.svg").status_code == 404
