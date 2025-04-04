"""The root external handler for the app.

Individual API groups are maintained in other modules.
"""

from .endpoints import router as root_router

__all__ = ["root_router"]
