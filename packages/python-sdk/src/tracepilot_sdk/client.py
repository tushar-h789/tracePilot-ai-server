"""Synchronous ingestion client.

- Endpoint and key come from arguments or the environment (`TRACEPILOT_ENDPOINT`,
  `TRACEPILOT_INGEST_KEY`). The key is never logged and `repr()` shows only its public prefix.
- Retries (bounded, exponential backoff with full jitter) only for connect errors, timeouts,
  429 (honouring `Retry-After`) and 5xx. Other 4xx responses are raised immediately.
  Retrying is safe because events carry `event_id`s and the server de-duplicates them.
- `send()` splits events into batches of at most `max_batch_size` events and `max_batch_bytes`.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from types import TracebackType
from typing import Any

import httpx

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
from tracepilot_sdk.models import MAX_BATCH_EVENTS, IngestResult, LogEvent

logger = logging.getLogger("tracepilot_sdk")

ENV_ENDPOINT = "TRACEPILOT_ENDPOINT"
ENV_KEY = "TRACEPILOT_INGEST_KEY"
DEFAULT_ENDPOINT = "http://localhost:8000"
INGEST_PATH = "/api/v1/ingest/logs"
DEFAULT_MAX_BATCH_BYTES = 1_000_000  # below the server's default 1 MiB body limit
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_VERSION = "0.1.0"

EventLike = LogEvent | Mapping[str, Any]


def _redacted(key: str) -> str:
    parts = key.split("_")
    if len(parts) >= 3 and parts[0] == "tp":
        return f"tp_{parts[1]}_***"
    return "***"


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form is not used by TracePilot
    return seconds if seconds >= 0 else None


class TracePilotClient:
    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        connect_timeout: float = 5.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        max_retry_after: float = 60.0,
        max_batch_size: int = MAX_BATCH_EVENTS,
        max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        key = api_key or os.environ.get(ENV_KEY)
        if not key:
            raise ConfigurationError(
                f"An ingestion key is required (pass api_key= or set {ENV_KEY})."
            )
        if not 1 <= max_batch_size <= MAX_BATCH_EVENTS:
            raise ConfigurationError(f"max_batch_size must be between 1 and {MAX_BATCH_EVENTS}.")
        if max_retries < 0 or max_retries > 10:
            raise ConfigurationError("max_retries must be between 0 and 10.")
        base = (endpoint or os.environ.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT).rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ConfigurationError("endpoint must start with http:// or https://")
        self.endpoint = base
        self._key = key
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.max_retry_after = max_retry_after
        self.max_batch_size = max_batch_size
        self.max_batch_bytes = max_batch_bytes
        self._sleep = sleep
        self._jitter = jitter
        self._http = httpx.Client(
            base_url=base,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            transport=transport,
            headers={"User-Agent": f"tracepilot-sdk-python/{_VERSION}"},
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return f"TracePilotClient(endpoint={self.endpoint!r}, api_key={_redacted(self._key)!r})"

    # --- lifecycle --------------------------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TracePilotClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- public API -------------------------------------------------------------------------
    def send(self, events: Iterable[EventLike]) -> IngestResult:
        """Send events in as many batches as needed; returns summed server counts."""
        result = IngestResult()
        for batch in self._chunks(events):
            result.add(self._post_batch(batch))
        return result

    # --- internals --------------------------------------------------------------------------
    def _chunks(self, events: Iterable[EventLike]) -> Iterator[list[dict[str, Any]]]:
        batch: list[dict[str, Any]] = []
        size = len(b'{"events":[]}')
        for item in events:
            event = item if isinstance(item, LogEvent) else LogEvent.model_validate(item)
            payload = event.to_payload()
            encoded = len(json.dumps(payload, separators=(",", ":")).encode()) + 1
            if batch and (
                len(batch) >= self.max_batch_size or size + encoded > self.max_batch_bytes
            ):
                yield batch
                batch, size = [], len(b'{"events":[]}')
            batch.append(payload)
            size += encoded
        if batch:
            yield batch

    def _backoff(self, attempt: int) -> float:
        exponential: float = self.backoff_base * float(2**attempt)
        return self._jitter() * min(self.backoff_max, exponential)

    def _post_batch(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps({"events": batch}, separators=(",", ":")).encode()
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        last_error: TracePilotError | None = None
        for attempt in range(self.max_retries + 1):
            delay: float | None = None
            try:
                response = self._http.post(INGEST_PATH, content=body, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = TransportError(f"Request timed out: {type(exc).__name__}")
            except httpx.TransportError as exc:
                last_error = TransportError(f"Network error: {type(exc).__name__}")
            else:
                if response.status_code < 300:
                    data: dict[str, Any] = response.json()
                    return data
                error = self._error_for(response)
                if not self._retryable(error):
                    raise error
                last_error = error
                if isinstance(error, RateLimitError) and error.retry_after is not None:
                    if error.retry_after > self.max_retry_after:
                        raise error
                    delay = error.retry_after
            if attempt == self.max_retries:
                break
            wait = delay if delay is not None else self._backoff(attempt)
            logger.warning(
                "tracepilot_retry",
                extra={
                    "attempt": attempt + 1,
                    "wait_seconds": round(wait, 3),
                    "error": type(last_error).__name__,
                },
            )
            self._sleep(wait)
        raise last_error or TransportError("No request was attempted.")

    @staticmethod
    def _retryable(error: ApiError) -> bool:
        return isinstance(error, (RateLimitError, ServerError))

    @staticmethod
    def _error_for(response: httpx.Response) -> ApiError:
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        if not isinstance(problem, dict):
            problem = {}
        detail = str(problem.get("detail") or response.reason_phrase or "Request failed")
        code = problem.get("code") if isinstance(problem.get("code"), str) else None
        status = response.status_code
        if status == 401:
            return AuthenticationError(detail, status_code=status, code=code)
        if status == 429:
            return RateLimitError(
                detail,
                retry_after=_parse_retry_after(response.headers.get("retry-after")),
                code=code,
            )
        if status in RETRYABLE_STATUS or status >= 500:
            return ServerError(detail, status_code=status, code=code)
        errors = problem.get("errors") if isinstance(problem.get("errors"), list) else None
        return ValidationError(detail, status_code=status, code=code, errors=errors)
