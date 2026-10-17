"""ORM model registry.

Import every model module here so `Base.metadata` is complete for Alembic autogenerate.
No business tables exist yet (Phase 1); the first ones arrive with auth in Phase 2.
"""

from app.db.base import Base

__all__ = ["Base"]
