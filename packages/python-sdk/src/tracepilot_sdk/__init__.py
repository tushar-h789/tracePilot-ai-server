"""TracePilot AI Python SDK: send structured log events to a TracePilot project."""

from tracepilot_sdk.client import TracePilotClient
from tracepilot_sdk.errors import (
    ApiError,
    AuthenticationError,
    ConfigurationError,
    RateLimitError,
    ServerError,
    TracePilotError,
    TransportError,
    ValidationError,
)
from tracepilot_sdk.models import IngestResult, LogEvent

__all__ = [
    "ApiError",
    "AuthenticationError",
    "ConfigurationError",
    "IngestResult",
    "LogEvent",
    "RateLimitError",
    "ServerError",
    "TracePilotClient",
    "TracePilotError",
    "TransportError",
    "ValidationError",
]
__version__ = "0.1.0"
