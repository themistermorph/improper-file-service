"""Domänenfehler – unabhängig von FastAPI, damit der Core überall nutzbar bleibt."""

from __future__ import annotations


class IFSError(Exception):
    """Basisklasse aller fachlichen Fehler."""

    http_status = 400

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.code = code or self.__class__.__name__


class NotFound(IFSError):
    http_status = 404


class PermissionDenied(IFSError):
    http_status = 403


class Conflict(IFSError):
    http_status = 409


class BadRequest(IFSError):
    http_status = 400


class QuotaExceeded(IFSError):
    http_status = 413


class PreconditionFailed(IFSError):
    http_status = 412
