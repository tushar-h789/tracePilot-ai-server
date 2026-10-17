"""Shared FastAPI dependencies (DI): services, client info, CSRF, current user, org scope.

Security boundary notes:
- `require_csrf` runs on every unsafe method of cookie-authenticated routers.
- `get_current_auth` validates the session cookie on every protected request (revocation,
  absolute expiry, idle timeout).
- `require_org_permission(...)` resolves the caller's membership from the database. A caller
  who is not a member gets 404 (no existence leak); a member lacking the permission gets 403.
  The same pattern is reused for projects in Phase 3 (see `ProjectScope` notes in ARCHITECTURE).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Path, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.csrf import HEADER_NAME, SAFE_METHODS, verify_csrf
from app.core.errors import (
    CsrfError,
    NotFoundError,
    OriginNotAllowedError,
    PayloadTooLargeError,
    PermissionDeniedError,
    UnsupportedMediaTypeError,
)
from app.core.net import ClientInfo, client_info
from app.core.passwords import PasswordService
from app.core.permissions import SENSITIVE_PERMISSIONS, Permission, Role, role_has
from app.core.rate_limit import FixedWindowRateLimiter
from app.db.session import get_db
from app.repositories.organizations import OrganizationRepository
from app.repositories.projects import ProjectRepository
from app.services.audit import AuditAction, AuditService
from app.services.auth import AuthContext, AuthService
from app.services.health import HealthService
from app.services.ingest import IngestContext, IngestService
from app.services.ingestion_keys import IngestionKeyService
from app.services.logs import LogService
from app.services.metrics import MetricsService
from app.services.organizations import OrganizationService, OrgScope
from app.services.projects import ProjectScope, ProjectService


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbSession = Annotated[Session, Depends(get_db)]


def get_health_service(request: Request) -> HealthService:
    return HealthService(engine=request.app.state.engine, redis_client=request.app.state.redis)


def get_client_info(request: Request, settings: SettingsDep) -> ClientInfo:
    return client_info(request, settings.trusted_proxies)


ClientDep = Annotated[ClientInfo, Depends(get_client_info)]


def get_audit_service(request: Request) -> AuditService:
    return AuditService(request.app.state.session_factory)


AuditDep = Annotated[AuditService, Depends(get_audit_service)]


def get_auth_service(
    request: Request, db: DbSession, settings: SettingsDep, audit: AuditDep
) -> AuthService:
    passwords: PasswordService = request.app.state.passwords
    limiter = FixedWindowRateLimiter(request.app.state.redis)
    return AuthService(db, settings, passwords, limiter, audit)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_org_service(db: DbSession, audit: AuditDep) -> OrganizationService:
    return OrganizationService(db, audit)


OrgServiceDep = Annotated[OrganizationService, Depends(get_org_service)]


def get_session_token(request: Request, settings: SettingsDep) -> str | None:
    return request.cookies.get(settings.session_cookie_name)


SessionTokenDep = Annotated[str | None, Depends(get_session_token)]


def require_csrf(request: Request, settings: SettingsDep, session_token: SessionTokenDep) -> None:
    """Signed double-submit check for unsafe methods (+ Origin allowlist when present)."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in settings.cors_origins:
        raise OriginNotAllowedError()
    if not verify_csrf(
        settings.secret_key.get_secret_value().encode(),
        header_value=request.headers.get(HEADER_NAME),
        cookie_value=request.cookies.get(settings.csrf_cookie_name),
        session_token=session_token,
    ):
        raise CsrfError()


def get_current_auth(service: AuthServiceDep, token: SessionTokenDep) -> AuthContext:
    return service.authenticate(token)


CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]


def authorize_org(
    *,
    db: Session,
    audit: AuditService,
    client: ClientInfo,
    auth: AuthContext,
    org_id: uuid.UUID,
    permission: Permission,
    not_found_message: str = "Organization not found.",
    target_type: str = "organization",
    target_id: uuid.UUID | None = None,
) -> OrgScope:
    """Resolve the caller's membership in `org_id` and check `permission`.

    Reusable building block: Phase 3's `require_project_permission` loads the project, then
    calls this with `project.org_id` (projects inherit the org role) and returns a ProjectScope.
    Non-members get 404 (indistinguishable from a missing org); members without the permission
    get 403, audited when the permission is security-sensitive.
    """
    membership = OrganizationRepository(db).get_membership(org_id, auth.user.id)
    if membership is None:
        raise NotFoundError(not_found_message)
    role = Role(membership.role)
    if not role_has(role, permission):
        if permission in SENSITIVE_PERMISSIONS:
            audit.record_independent(
                AuditAction.PERMISSION_DENIED,
                actor_user_id=auth.user.id,
                org_id=org_id,
                target_type=target_type,
                target_id=target_id or org_id,
                ip_address=client.ip,
                details={"permission": permission.value, "role": role.value},
            )
        raise PermissionDeniedError()
    return OrgScope(org_id=org_id, user_id=auth.user.id, role=role)


def require_org_permission(permission: Permission) -> Callable[..., OrgScope]:
    """Dependency factory: `scope: Annotated[OrgScope, Depends(require_org_permission(P))]`."""

    def dependency(
        org_id: Annotated[uuid.UUID, Path()],
        auth: CurrentAuth,
        db: DbSession,
        audit: AuditDep,
        client: ClientDep,
    ) -> OrgScope:
        return authorize_org(
            db=db, audit=audit, client=client, auth=auth, org_id=org_id, permission=permission
        )

    dependency.__name__ = f"require_{permission.name.lower()}"
    return dependency


# --- Phase 3: projects, ingestion keys, ingestion -------------------------------------------


def get_project_service(db: DbSession, audit: AuditDep) -> ProjectService:
    return ProjectService(db, audit)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_log_service(db: DbSession) -> LogService:
    return LogService(db)


LogServiceDep = Annotated[LogService, Depends(get_log_service)]


def get_metrics_service(db: DbSession) -> MetricsService:
    return MetricsService(db)


MetricsServiceDep = Annotated[MetricsService, Depends(get_metrics_service)]


def get_ingestion_key_service(
    db: DbSession, audit: AuditDep, settings: SettingsDep
) -> IngestionKeyService:
    return IngestionKeyService(db, audit, settings)


IngestionKeyServiceDep = Annotated[IngestionKeyService, Depends(get_ingestion_key_service)]

PROJECT_NOT_FOUND = "Project not found."


def authorize_project(
    *,
    db: Session,
    audit: AuditService,
    client: ClientInfo,
    auth: AuthContext,
    project_id: uuid.UUID,
    permission: Permission,
) -> ProjectScope:
    """Load the project, then authorize against its organization (role is inherited).

    A project in an organization the caller does not belong to is a 404, identical to an
    unknown id. Archived projects are still visible to members (read-only).
    """
    project = ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError(PROJECT_NOT_FOUND)
    org_scope = authorize_org(
        db=db,
        audit=audit,
        client=client,
        auth=auth,
        org_id=project.org_id,
        permission=permission,
        not_found_message=PROJECT_NOT_FOUND,
        target_type="project",
        target_id=project.id,
    )
    return ProjectScope(
        org_id=project.org_id,
        project_id=project.id,
        user_id=auth.user.id,
        role=org_scope.role,
        project=project,
    )


def require_project_permission(permission: Permission) -> Callable[..., ProjectScope]:
    """Dependency factory for project-scoped routes (see `require_org_permission`)."""

    def dependency(
        project_id: Annotated[uuid.UUID, Path()],
        auth: CurrentAuth,
        db: DbSession,
        audit: AuditDep,
        client: ClientDep,
    ) -> ProjectScope:
        return authorize_project(
            db=db,
            audit=audit,
            client=client,
            auth=auth,
            project_id=project_id,
            permission=permission,
        )

    dependency.__name__ = f"require_project_{permission.name.lower()}"
    return dependency


def get_ingest_service(request: Request, db: DbSession, settings: SettingsDep) -> IngestService:
    return IngestService(db, settings, FixedWindowRateLimiter(request.app.state.redis))


IngestServiceDep = Annotated[IngestService, Depends(get_ingest_service)]


def get_ingest_context(request: Request, service: IngestServiceDep) -> IngestContext:
    """Ingestion auth: only the Authorization header counts; cookies are never consulted."""
    return service.authenticate(request.headers.get("authorization"))


IngestContextDep = Annotated[IngestContext, Depends(get_ingest_context)]


async def read_ingest_body(request: Request, settings: SettingsDep) -> bytes:
    """Read the raw body with a hard size cap *before* any JSON parsing (413 when exceeded)."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise UnsupportedMediaTypeError("Content-Type must be application/json.")
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise UnsupportedMediaTypeError("Compressed request bodies are not supported.")
    limit = settings.ingest_max_body_bytes
    too_large = f"Request body must be at most {limit} bytes."
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError(too_large)
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeError(too_large)
        chunks.append(chunk)
    return b"".join(chunks)


IngestBodyDep = Annotated[bytes, Depends(read_ingest_body)]
