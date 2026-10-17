"""Exception hierarchy. Messages never contain the ingestion key."""

from __future__ import annotations


class TracePilotError(Exception):
    """Base class for every SDK error."""


class ConfigurationError(TracePilotError):
    """Missing or invalid client configuration (e.g. no ingestion key)."""


class TransportError(TracePilotError):
    """Network failure or timeout after all retries."""


class ApiError(TracePilotError):
    """The API answered with an error status."""

    def __init__(self, message: str, *, status_code: int, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class AuthenticationError(ApiError):
    """401: the ingestion key is missing, unknown, revoked, expired or its project is archived."""


class ValidationError(ApiError):
    """400/413/415/422: the batch was rejected; nothing was stored. Never retried."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        errors: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, code=code)
        self.errors = errors or []


class RateLimitError(ApiError):
    """429 after all retries. `retry_after` is the server's hint in seconds (if any)."""

    def __init__(self, message: str, *, retry_after: float | None, code: str | None = None) -> None:
        super().__init__(message, status_code=429, code=code)
        self.retry_after = retry_after


class ServerError(ApiError):
    """5xx after all retries."""
