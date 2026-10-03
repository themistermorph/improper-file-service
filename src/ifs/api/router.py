"""Aggregation aller API-Router unter dem Präfix /api."""

from fastapi import APIRouter

from . import (
    admin,
    archive,
    auth,
    bulk,
    entries,
    monitor,
    preview,
    published,
    roles,
    shares,
    trash,
    uploads,
    users,
    versions,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(entries.router)
api_router.include_router(versions.router)
api_router.include_router(bulk.router)
api_router.include_router(preview.router)
api_router.include_router(archive.router)
api_router.include_router(trash.router)
api_router.include_router(uploads.router)
api_router.include_router(shares.router)
api_router.include_router(published.router)
api_router.include_router(admin.router)
api_router.include_router(monitor.router)
