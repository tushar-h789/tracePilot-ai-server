from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_health_service
from app.schemas.health import LivenessResponse, ReadinessResponse
from app.services.health import HealthService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
def live() -> LivenessResponse:
    """The process is up and serving requests. Does not touch any dependency."""
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe (Postgres + Redis)",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
def ready(
    response: Response, service: Annotated[HealthService, Depends(get_health_service)]
) -> ReadinessResponse:
    report = service.readiness()
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    response.headers["Cache-Control"] = "no-store"
    return report
