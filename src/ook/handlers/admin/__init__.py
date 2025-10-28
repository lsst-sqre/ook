"""Admin API endpoints for Ook.

These endpoints provide administrative operations that should be protected
via Gafaelfawr ingress configuration. The admin API mirrors the structure
of the main API but provides write/delete operations for manual intervention.
"""

__all__ = ["admin_router"]

from fastapi import APIRouter

from ook.config import config

from .authors import admin_authors_router

admin_router = APIRouter(prefix=f"{config.path_prefix}/admin", tags=["admin"])
"""FastAPI router for all admin handlers."""

# Include sub-routers
admin_router.include_router(admin_authors_router)
