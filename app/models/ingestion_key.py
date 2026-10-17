from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import created_at_col, uuid_pk


class IngestionKey(Base):
    """Project-scoped ingestion credential. Only an HMAC-SHA256 of the secret is stored.

    `prefix` is the public, non-secret part of the key (`tp_<public id>`) used for lookup and
    display; the plaintext key is returned exactly once at creation/rotation.
    """

    __tablename__ = "ingestion_keys"
    __table_args__ = (
        CheckConstraint("char_length(key_hash) = 64", name="key_hash_length"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 100", name="name_length"),
        Index(
            "ix_ingestion_keys_project_id_active",
            "project_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a rotation keeps the old key valid for a grace period; NULL = no expiry.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_keys.id", ondelete="SET NULL")
    )

    def status_at(self, now: datetime) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        return "active"

    def __repr__(self) -> str:  # never include the hash
        return f"<IngestionKey id={self.id} prefix={self.prefix}>"
