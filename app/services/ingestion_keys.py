"""Ingestion key lifecycle: create (secret shown once), list, revoke, rotate.

Rotation (ADR-021): a new key with the same name is created; the old key is revoked
immediately (grace_seconds=0, default) or keeps working until `expires_at` (grace period,
capped by KEY_ROTATION_MAX_GRACE_SECONDS). The old key's `replaced_by_id` points at the new one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.ingest_keys import GeneratedKey, generate_key, hash_key
from app.core.net import ClientInfo
from app.core.pagination import decode_cursor, next_cursor_for
from app.models.ingestion_key import IngestionKey
from app.repositories.ingestion_keys import IngestionKeyRepository
from app.repositories.projects import ProjectRepository
from app.services.audit import AuditAction, AuditService
from app.services.projects import ARCHIVED_READ_ONLY, ProjectScope


@dataclass(frozen=True, slots=True)
class KeyPage:
    items: list[IngestionKey]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class IssuedKey:
    key: IngestionKey
    plaintext: str

    def __repr__(self) -> str:
        return f"IssuedKey(key={self.key!r}, plaintext='[REDACTED]')"


class IngestionKeyService:
    def __init__(self, db: Session, audit: AuditService, settings: Settings) -> None:
        self.db = db
        self.audit = audit
        self.settings = settings
        self.keys = IngestionKeyRepository(db)
        self.projects = ProjectRepository(db)
        self._pepper = settings.ingest_key_pepper.get_secret_value().encode()

    def list(
        self, scope: ProjectScope, *, include_revoked: bool, cursor: str | None, limit: int
    ) -> KeyPage:
        rows = self.keys.list_for_project(
            scope.project_id,
            include_revoked=include_revoked,
            before_id=decode_cursor(cursor),
            limit=limit,
        )
        return KeyPage(items=rows[:limit], next_cursor=next_cursor_for([r.id for r in rows], limit))

    def _lock_writable_project(self, scope: ProjectScope) -> None:
        # Serialises key creation per project so the active-key limit cannot be raced.
        self.projects.lock(scope.project_id)
        self.db.refresh(scope.project)
        if scope.project.is_archived:
            raise ConflictError(ARCHIVED_READ_ONLY)

    def _new_key(self, scope: ProjectScope, name: str) -> IssuedKey:
        generated: GeneratedKey = generate_key()
        while self.keys.prefix_exists(generated.prefix):  # pragma: no cover - 2^-48 per try
            generated = generate_key()
        key = self.keys.add(
            project_id=scope.project_id,
            name=name,
            prefix=generated.prefix,
            key_hash=hash_key(self._pepper, generated.plaintext),
            created_by=scope.user_id,
        )
        return IssuedKey(key=key, plaintext=generated.plaintext)

    def create(self, scope: ProjectScope, *, name: str, client: ClientInfo) -> IssuedKey:
        self._lock_writable_project(scope)
        now = datetime.now(UTC)
        limit = self.settings.max_active_keys_per_project
        if self.keys.count_active(scope.project_id, now) >= limit:
            raise ConflictError(
                f"This project already has {limit} active ingestion keys. Revoke one first."
            )
        issued = self._new_key(scope, " ".join(name.split()))
        self.audit.record(
            self.db,
            AuditAction.INGESTION_KEY_CREATED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="ingestion_key",
            target_id=issued.key.id,
            ip_address=client.ip,
            details={"project_id": str(scope.project_id), "prefix": issued.key.prefix},
        )
        self.db.commit()
        self.db.refresh(issued.key)
        return issued

    def _get(self, scope: ProjectScope, key_id: uuid.UUID) -> IngestionKey:
        key = self.keys.get_in_project(scope.project_id, key_id)
        if key is None:
            raise NotFoundError("Ingestion key not found.")
        return key

    def revoke(self, scope: ProjectScope, *, key_id: uuid.UUID, client: ClientInfo) -> IngestionKey:
        key = self._get(scope, key_id)
        if key.revoked_at is not None:
            return key  # idempotent
        key.revoked_at = datetime.now(UTC)
        self.audit.record(
            self.db,
            AuditAction.INGESTION_KEY_REVOKED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="ingestion_key",
            target_id=key.id,
            ip_address=client.ip,
            details={"project_id": str(scope.project_id), "prefix": key.prefix},
        )
        self.db.commit()
        self.db.refresh(key)
        return key

    def rotate(
        self, scope: ProjectScope, *, key_id: uuid.UUID, grace_seconds: int, client: ClientInfo
    ) -> tuple[IssuedKey, IngestionKey]:
        if grace_seconds > self.settings.key_rotation_max_grace_seconds:
            limit = self.settings.key_rotation_max_grace_seconds
            raise ValidationFailedError(
                f"grace_seconds must be at most {limit}.",
                errors=[
                    {
                        "loc": ["body", "grace_seconds"],
                        "msg": f"Must be at most {limit}.",
                        "type": "less_than_equal",
                    }
                ],
            )
        self._lock_writable_project(scope)
        old = self._get(scope, key_id)
        now = datetime.now(UTC)
        if old.status_at(now) != "active":
            raise ConflictError("Only active keys can be rotated.")
        issued = self._new_key(scope, old.name)
        if grace_seconds == 0:
            old.revoked_at = now
        else:
            old.expires_at = now + timedelta(seconds=grace_seconds)
        old.replaced_by_id = issued.key.id
        self.audit.record(
            self.db,
            AuditAction.INGESTION_KEY_ROTATED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="ingestion_key",
            target_id=old.id,
            ip_address=client.ip,
            details={
                "project_id": str(scope.project_id),
                "previous_prefix": old.prefix,
                "new_prefix": issued.key.prefix,
                "grace_seconds": grace_seconds,
            },
        )
        self.db.commit()
        self.db.refresh(old)
        self.db.refresh(issued.key)
        return issued, old
