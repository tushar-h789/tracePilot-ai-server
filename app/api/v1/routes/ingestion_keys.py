"""Ingestion key management (cookie auth + CSRF). Secrets appear only in create/rotate responses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    ClientDep,
    IngestionKeyServiceDep,
    require_csrf,
    require_project_permission,
)
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.core.permissions import Permission
from app.models.ingestion_key import IngestionKey
from app.schemas.common import problem_responses
from app.schemas.ingestion_keys import (
    IngestionKeyCreate,
    IngestionKeyCreated,
    IngestionKeyListResponse,
    IngestionKeyOut,
    IngestionKeyRotate,
    IngestionKeyRotated,
)
from app.services.projects import ProjectScope

router = APIRouter(
    prefix="/projects/{project_id}/ingestion-keys",
    tags=["ingestion-keys"],
    dependencies=[Depends(require_csrf)],
)

KeysRead = Annotated[
    ProjectScope, Depends(require_project_permission(Permission.KEYS_READ_METADATA))
]
KeysManage = Annotated[ProjectScope, Depends(require_project_permission(Permission.KEYS_MANAGE))]


def key_out(key: IngestionKey) -> IngestionKeyOut:
    return IngestionKeyOut(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        prefix=key.prefix,
        status=key.status_at(datetime.now(UTC)),  # type: ignore[arg-type]
        created_by=key.created_by,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        revoked_at=key.revoked_at,
        replaced_by_id=key.replaced_by_id,
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.get(
    "",
    response_model=IngestionKeyListResponse,
    summary="List ingestion keys (metadata only; member+)",
    responses=problem_responses(401, 404, 422),
)
def list_keys(
    scope: KeysRead,
    service: IngestionKeyServiceDep,
    include_revoked: bool = False,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> IngestionKeyListResponse:
    page = service.list(scope, include_revoked=include_revoked, cursor=cursor, limit=limit)
    return IngestionKeyListResponse(
        items=[key_out(k) for k in page.items], next_cursor=page.next_cursor
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=IngestionKeyCreated,
    summary="Create an ingestion key (owner/admin). The secret is returned only here.",
    responses=problem_responses(401, 403, 404, 409, 422),
)
def create_key(
    body: IngestionKeyCreate,
    scope: KeysManage,
    service: IngestionKeyServiceDep,
    client: ClientDep,
    response: Response,
) -> IngestionKeyCreated:
    issued = service.create(scope, name=body.name, client=client)
    _no_store(response)
    return IngestionKeyCreated(key=key_out(issued.key), secret=issued.plaintext)


@router.post(
    "/{key_id}/revoke",
    response_model=IngestionKeyOut,
    summary="Revoke an ingestion key immediately (owner/admin; idempotent)",
    responses=problem_responses(401, 403, 404, 422),
)
def revoke_key(
    key_id: uuid.UUID, scope: KeysManage, service: IngestionKeyServiceDep, client: ClientDep
) -> IngestionKeyOut:
    return key_out(service.revoke(scope, key_id=key_id, client=client))


@router.post(
    "/{key_id}/rotate",
    status_code=status.HTTP_201_CREATED,
    response_model=IngestionKeyRotated,
    summary="Rotate: issue a replacement key; the old one is revoked now or after a grace period",
    responses=problem_responses(401, 403, 404, 409, 422),
)
def rotate_key(
    key_id: uuid.UUID,
    scope: KeysManage,
    service: IngestionKeyServiceDep,
    client: ClientDep,
    response: Response,
    body: IngestionKeyRotate | None = None,
) -> IngestionKeyRotated:
    grace = body.grace_seconds if body is not None else 0
    issued, old = service.rotate(scope, key_id=key_id, grace_seconds=grace, client=client)
    _no_store(response)
    return IngestionKeyRotated(
        key=key_out(issued.key), secret=issued.plaintext, previous_key=key_out(old)
    )
