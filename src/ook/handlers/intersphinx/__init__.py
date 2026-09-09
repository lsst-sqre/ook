"""Intersphinx inventory cache and documentation source registry API."""

from .endpoints import router as intersphinx_router
from .sources import router as sources_router

# The registry lives under the cache's own path prefix, so it is mounted
# on that router rather than given a second top-level one.
intersphinx_router.include_router(sources_router)

__all__ = [
    "intersphinx_router",
]
