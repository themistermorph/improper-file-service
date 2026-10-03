"""FastAPI-Applikation."""

from __future__ import annotations

import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import MutableHeaders

from . import __version__
from .api import system
from .api.deps import is_secure_request
from .api.router import api_router
from .config import get_settings
from .core import content, namespace, roles
from .db import create_all, session_scope
from .errors import IFSError
from .models import User
from .security import hash_password

logger = logging.getLogger("ifs")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "frame-src 'self' blob:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'"
)

# Statische Security-Header (Reihenfolge wie zuvor gesetzt).
_STATIC_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
)


class SecurityHeadersMiddleware:
    """Reine ASGI-Middleware für Sicherheits-Header.

    Gegenüber ``BaseHTTPMiddleware`` (``@app.middleware("http")``) entfallen die
    Task-Group-/Stream-Wrapper pro Request; Header, Streaming-Verhalten und
    Fehlerweitergabe bleiben unverändert.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in _STATIC_SECURITY_HEADERS:
                    headers.setdefault(key, value)
                if is_secure_request(Request(scope)):
                    headers.setdefault(
                        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def seed_initial_data(username: str | None = None, password: str | None = None) -> None:
    """Legt beim ersten Start einen Admin und das Wurzelverzeichnis an.

    Ein Admin wird nur erstellt, wenn ein Passwort gesetzt ist (Umgebungsvariable
    ``IFS_ADMIN_PASSWORD`` oder expliziter Parameter) – kein unsicheres Default-Passwort.
    """
    settings = get_settings()
    admin_username = username or settings.admin_username
    admin_password = password if password is not None else settings.admin_password

    with session_scope() as db:
        existing = db.execute(select(User.id).limit(1)).first()
        if existing is not None:
            return
        if not admin_password:
            logger.warning(
                "Kein Admin angelegt: IFS_ADMIN_PASSWORD ist nicht gesetzt. Bitte in .env "
                "setzen oder `python -m ifs.cli seed-admin --password <pw>` ausführen."
            )
            return
        admin = User(
            username=admin_username,
            password_hash=hash_password(admin_password),
            is_admin=True,
        )
        db.add(admin)
        db.flush()
        namespace.ensure_root(db, admin.id)
        logger.warning(
            "Initialer Admin '%s' angelegt – Passwort nach dem ersten Login ändern!",
            admin_username,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_schema:
        create_all()
    seed_initial_data()
    with session_scope() as db:
        roles.ensure_builtin_roles(db)
    try:
        content.ensure_bucket()
    except Exception as exc:  # pragma: no cover - Start soll nicht hart scheitern
        logger.warning("Bucket konnte nicht geprüft/angelegt werden: %s", exc)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="IFS – Webbasiertes Dateisystem",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.expose_docs else None,
        redoc_url="/redoc" if settings.expose_docs else None,
        openapi_url="/openapi.json" if settings.expose_docs else None,
    )

    @app.exception_handler(IFSError)
    async def _ifs_error_handler(request: Request, exc: IFSError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        # Ungültige Eingaben (z. B. Range/Offset/Pfad) dürfen keinen 500 erzeugen.
        return JSONResponse(
            status_code=400, content={"code": "BadRequest", "message": "Ungültige Anfrage"}
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error_handler(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        # Eindeutigkeitsverletzungen (z. B. Namenskonflikt) als 409 ausgeben.
        return JSONResponse(
            status_code=409, content={"code": "Conflict", "message": "Konflikt (Eindeutigkeit)"}
        )

    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(api_router)
    app.include_router(system.router)

    web_dir = pathlib.Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()
