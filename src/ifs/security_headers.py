"""Content-Security-Policy ohne ``unsafe-inline``.

Die Web-Oberfläche besteht bewusst aus jeweils einer HTML-Datei (``index``,
``mobile``, ``published``) und enthält Inline-``<script>``/``<style>``-Blöcke.
Statt ``'unsafe-inline'`` zu erlauben, werden die Blöcke beim Start gelesen und
ihre SHA-256-Hashes in die Policy aufgenommen (CSP3). Das funktioniert mit dem
statischen ``StaticFiles``-Mount, ohne Nonce-Templating und ohne die
Single-File-Eigenschaft der mobilen Ansicht aufzugeben.

Wichtig: Die Hashes werden nur beim Start berechnet. Änderungen an den
HTML-Dateien erfordern daher einen Neustart des API-Prozesses – im Container
geschieht das durch ``docker compose up -d api`` ohnehin.

Event-Handler-Attribute (``onclick=``) und ``style=``-Attribute sind in der
Oberfläche vollständig entfernt; sie lassen sich nicht sinnvoll hashen und
würden ``'unsafe-inline'`` bzw. ``'unsafe-hashes'`` erzwingen. Dynamische Werte
werden per CSSOM (``element.style.*``) gesetzt, was von CSP nicht blockiert wird.
"""

from __future__ import annotations

import base64
import hashlib
import re
from functools import lru_cache
from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"

# ``<script ...>...</script>`` bzw. ``<style ...>...</style>``. Der Inhalt wird
# byte-genau gehasht, weil Browser exakt die ausgelieferten Bytes prüfen.
_INLINE_RE = re.compile(rb"<(script|style)([^>]*)>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
_EXTERNAL_SRC_RE = re.compile(rb"\bsrc\s*=", re.IGNORECASE)


def _inline_hashes(web_dir: Path) -> tuple[list[str], list[str]]:
    """Liefert ``(script_hashes, style_hashes)`` für alle HTML-Dateien."""
    scripts: list[str] = []
    styles: list[str] = []
    for path in sorted(web_dir.glob("*.html")):
        data = path.read_bytes()
        for match in _INLINE_RE.finditer(data):
            tag = match.group(1).lower()
            attrs = match.group(2)
            # Externe Skripte werden über 'self' erlaubt, nicht über einen Hash.
            if tag == b"script" and _EXTERNAL_SRC_RE.search(attrs):
                continue
            digest = base64.b64encode(hashlib.sha256(match.group(3)).digest()).decode("ascii")
            value = f"'sha256-{digest}'"
            bucket = scripts if tag == b"script" else styles
            if value not in bucket:
                bucket.append(value)
    return scripts, styles


def build_csp(
    script_hashes: list[str] | None = None,
    style_hashes: list[str] | None = None,
) -> str:
    """Baut die Policy; ohne Argumente aus den ausgelieferten HTML-Dateien."""
    if script_hashes is None or style_hashes is None:
        default_scripts, default_styles = _inline_hashes(WEB_DIR)
        script_hashes = default_scripts if script_hashes is None else script_hashes
        style_hashes = default_styles if style_hashes is None else style_hashes

    script_src = " ".join(["'self'", *script_hashes])
    style_src = " ".join(["'self'", *style_hashes])
    return (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "script-src-attr 'none'; "
        f"style-src {style_src}; "
        "style-src-attr 'none'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "frame-src 'self' blob:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'self'; "
        "form-action 'self'"
    )


@lru_cache(maxsize=1)
def content_security_policy() -> str:
    """Einmalig berechnete Policy (Hashes der aktuellen HTML-Dateien)."""
    return build_csp()
