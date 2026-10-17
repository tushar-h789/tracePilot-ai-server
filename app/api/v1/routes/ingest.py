"""Public ingestion endpoint. Authenticated ONLY by an ingestion key (Authorization: Bearer).

No cookies, no CSRF: the endpoint never reads cookies, and CORS does not allow the
Authorization header, so browsers cannot call it cross-origin with a user's credentials.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import IngestBodyDep, IngestContextDep, IngestServiceDep
from app.schemas.common import problem_responses
from app.schemas.ingest import IngestBatch, IngestResult

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post(
    "/logs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResult,
    summary="Ingest a batch of log events (ingestion key auth; idempotent per event_id)",
    responses={
        **problem_responses(401, 413, 415, 422, 429, 503),
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/IngestBatch"}}
            },
        },
        "security": [{"IngestionKey": []}],
    },
)
def ingest_logs(
    ctx: IngestContextDep, body: IngestBodyDep, service: IngestServiceDep
) -> IngestResult:
    return service.ingest(ctx, body)


# Referenced from openapi_extra so the contract (and the generated client types) include it.
__all__ = ["IngestBatch", "router"]
