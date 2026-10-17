"""Domain errors mapped to RFC 9457-style problem+json responses.

Services raise these; routes stay thin. Messages are user-safe and generic where it matters
(authentication), and never include secrets or raw input values.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 400
    title: str = "Bad Request"
    code: str = "bad_request"

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.detail = detail or self.title
        self.headers = headers or {}
        self.errors = errors
        super().__init__(self.detail)


class NotAuthenticatedError(AppError):
    status_code = 401
    title = "Unauthorized"
    code = "not_authenticated"

    def __init__(self, detail: str = "Authentication required.") -> None:
        super().__init__(detail)


class InvalidCredentialsError(AppError):
    status_code = 401
    title = "Unauthorized"
    code = "invalid_credentials"

    def __init__(self) -> None:
        # One message for unknown email, wrong password and inactive account (no enumeration).
        super().__init__("Invalid email or password.")


class CsrfError(AppError):
    status_code = 403
    title = "Forbidden"
    code = "csrf_failed"

    def __init__(self) -> None:
        super().__init__("CSRF validation failed. Refresh the page and try again.")


class OriginNotAllowedError(AppError):
    status_code = 403
    title = "Forbidden"
    code = "origin_not_allowed"

    def __init__(self) -> None:
        super().__init__("Request origin is not allowed.")


class InvalidIngestionKeyError(AppError):
    status_code = 401
    title = "Unauthorized"
    code = "invalid_ingestion_key"

    def __init__(self) -> None:
        # One message for missing, malformed, unknown, revoked, expired and archived-project keys.
        super().__init__(
            "A valid ingestion key is required.", headers={"WWW-Authenticate": "Bearer"}
        )


class PermissionDeniedError(AppError):
    status_code = 403
    title = "Forbidden"
    code = "permission_denied"

    def __init__(self, detail: str = "You do not have permission to perform this action.") -> None:
        super().__init__(detail)


class NotFoundError(AppError):
    status_code = 404
    title = "Not Found"
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    title = "Conflict"
    code = "conflict"


class PayloadTooLargeError(AppError):
    status_code = 413
    title = "Content Too Large"
    code = "payload_too_large"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    title = "Unsupported Media Type"
    code = "unsupported_media_type"


class ValidationFailedError(AppError):
    status_code = 422
    title = "Unprocessable Content"
    code = "validation_failed"


class RateLimitedError(AppError):
    status_code = 429
    title = "Too Many Requests"
    code = "rate_limited"

    def __init__(
        self, retry_after: int, detail: str = "Too many attempts. Please wait and try again."
    ) -> None:
        super().__init__(
            detail,
            headers={"Retry-After": str(max(1, retry_after))},
        )
        self.retry_after = max(1, retry_after)


class ServiceUnavailableError(AppError):
    status_code = 503
    title = "Service Unavailable"
    code = "service_unavailable"
