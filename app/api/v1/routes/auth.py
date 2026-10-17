"""Auth routes (thin): parse input, call AuthService, set/clear cookies."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status

from app.api.cookies import clear_session_cookies, set_csrf_cookie, set_session_cookies
from app.api.deps import (
    AuthServiceDep,
    ClientDep,
    CurrentAuth,
    SessionTokenDep,
    SettingsDep,
    require_csrf,
)
from app.core.config import Settings
from app.models.session import UserSession
from app.models.user import User
from app.schemas.auth import (
    CsrfResponse,
    LoginRequest,
    LogoutAllResponse,
    MeResponse,
    RegisterRequest,
    SessionInfo,
    SessionListResponse,
    SessionOut,
    UserOut,
)
from app.schemas.common import MessageResponse, problem_responses

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_csrf)])

REGISTER_MESSAGE = (
    "Registration received. If this email address was not already registered, "
    "your account is ready and you can sign in."
)


def _me(user: User, session: UserSession, settings: Settings) -> MeResponse:
    return MeResponse(
        user=UserOut.model_validate(user, from_attributes=True),
        session=SessionInfo(
            expires_at=session.expires_at,
            idle_timeout_seconds=settings.session_idle_timeout_minutes * 60,
        ),
    )


@router.get("/csrf", response_model=CsrfResponse, summary="Issue a CSRF token (sets cookie)")
def issue_csrf(
    response: Response, settings: SettingsDep, session_token: SessionTokenDep
) -> CsrfResponse:
    return CsrfResponse(csrf_token=set_csrf_cookie(response, settings, session_token))


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageResponse,
    summary="Register (generic response; never reveals whether the email exists)",
    responses=problem_responses(403, 422, 429, 503),
)
def register(body: RegisterRequest, service: AuthServiceDep, client: ClientDep) -> MessageResponse:
    service.register(
        email=body.email, password=body.password, display_name=body.display_name, client=client
    )
    return MessageResponse(detail=REGISTER_MESSAGE)


@router.post(
    "/login",
    response_model=MeResponse,
    summary="Log in (sets HttpOnly session cookie + rotated CSRF cookie)",
    responses=problem_responses(401, 403, 429, 503),
)
def login(
    body: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    client: ClientDep,
    settings: SettingsDep,
    session_token: SessionTokenDep,
) -> MeResponse:
    result = service.login(
        email=body.email, password=body.password, client=client, current_token=session_token
    )
    set_session_cookies(response, settings, result.token)
    return _me(result.user, result.session, settings)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out (revokes the session server-side; idempotent)",
    responses=problem_responses(403),
)
def logout(
    response: Response,
    service: AuthServiceDep,
    client: ClientDep,
    settings: SettingsDep,
    session_token: SessionTokenDep,
) -> None:
    service.logout(session_token, client)
    clear_session_cookies(response, settings)


@router.post(
    "/logout-all",
    response_model=LogoutAllResponse,
    summary="Revoke every session of the current user",
    responses=problem_responses(401, 403),
)
def logout_all(
    response: Response,
    auth: CurrentAuth,
    service: AuthServiceDep,
    client: ClientDep,
    settings: SettingsDep,
) -> LogoutAllResponse:
    count = service.logout_all(auth, client)
    clear_session_cookies(response, settings)
    return LogoutAllResponse(revoked_sessions=count)


@router.get(
    "/me", response_model=MeResponse, summary="Current user", responses=problem_responses(401)
)
def me(auth: CurrentAuth, settings: SettingsDep) -> MeResponse:
    return _me(auth.user, auth.session, settings)


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List my active sessions",
    responses=problem_responses(401),
)
def list_sessions(auth: CurrentAuth, service: AuthServiceDep) -> SessionListResponse:
    rows = service.list_sessions(auth)
    return SessionListResponse(
        items=[
            SessionOut(
                id=row.id,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                ip_address=row.ip_address,
                user_agent=row.user_agent,
                current=row.id == auth.session.id,
            )
            for row in rows
        ]
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one of my sessions",
    responses=problem_responses(401, 403, 404),
)
def revoke_session(
    session_id: uuid.UUID,
    response: Response,
    auth: CurrentAuth,
    service: AuthServiceDep,
    client: ClientDep,
    settings: SettingsDep,
) -> None:
    if service.revoke_session(auth, session_id, client):
        clear_session_cookies(response, settings)
