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
from app.core.errors import CsrfError, NotFoundError, OriginNotAllowedError, PermissionDeniedError
from app.core.net import ClientInfo, client_info
from app.core.passwords import PasswordService
from app.core.permissions import SENSITIVE_PERMISSIONS, Permission, Role, role_has
from app.core.rate_limit import FixedWindowRateLimiter
from app.db.session import get_db
from app.repositories.organizations import OrganizationRepository
from app.services.audit import AuditAction, AuditService
from app.services.auth import AuthContext, AuthService
from app.services.health import HealthService
from app.services.organizations import OrganizationService, OrgScope


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
) -> OrgScope:
    """Resolve the caller's membership in `org_id` and check `permission`.

    Reusable building block: Phase 3's `require_project_permission` loads the project, then
    calls this with `project.org_id` (projects inherit the org role) and returns a ProjectScope.
    Non-members get 404 (indistinguishable from a missing org); members without the permission
    get 403, audited when the permission is security-sensitive.
    """
    membership = OrganizationRepository(db).get_membership(org_id, auth.user.id)
    if membership is None:
        raise NotFoundError("Organization not found.")
    role = Role(membership.role)
    if not role_has(role, permission):
        if permission in SENSITIVE_PERMISSIONS:
            audit.record_independent(
                AuditAction.PERMISSION_DENIED,
                actor_user_id=auth.user.id,
                org_id=org_id,
                target_type="organization",
                target_id=org_id,
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
