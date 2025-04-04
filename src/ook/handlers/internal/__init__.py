"""Internal HTTP handlers that serve relative to the root path, ``/``.

These handlers aren't externally visible since the app is available at a path,
``/ook``.
"""

from .endpoints import router as internal_router

__all__ = ["internal_router"]
