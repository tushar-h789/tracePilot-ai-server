"""Baseline: empty schema (Phase 1). Business tables start in Phase 2.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-17 00:00:00+00:00
"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Intentionally empty: establishes the revision chain and `alembic_version` table."""


def downgrade() -> None:
    """Nothing to undo."""
