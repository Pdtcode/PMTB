"""
Kalshi API error types and retry decorator.

Error hierarchy:
    KalshiAPIError (base)
    ├── KalshiRateLimitError  — 429 responses (retried)
    ├── KalshiServerError     — 5xx responses (retried)
    └── KalshiClientError     — 4xx non-429 responses (raised immediately)

kalshi_retry decorator: retries KalshiRateLimitError and KalshiServerError
with exponential backoff + jitter up to 5 attempts. KalshiClientError is
never retried.
"""
from __future__ import annotations

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)


class KalshiAPIError(Exception):
    """Base class for all Kalshi API errors."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Kalshi API error {status_code}: {message}")


class KalshiRateLimitError(KalshiAPIError):
    """Raised on 429 Too Many Requests responses."""


class KalshiServerError(KalshiAPIError):
    """Raised on 5xx server error responses."""


class KalshiClientError(KalshiAPIError):
    """Raised on 4xx client error responses (excluding 429)."""


def classify_error(status_code: int, message: str) -> KalshiAPIError:
    """
    Map an HTTP status code to the appropriate KalshiAPIError subclass.

    Args:
        status_code: HTTP response status code.
        message:     Error message from response body.

    Returns:
        The appropriate KalshiAPIError subclass instance.
    """
    if status_code == 429:
        return KalshiRateLimitError(status_code, message)
    elif 500 <= status_code < 600:
        return KalshiServerError(status_code, message)
    else:
        return KalshiClientError(status_code, message)


kalshi_retry = retry(
    wait=wait_exponential_jitter(initial=1, max=30, jitter=3),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((KalshiRateLimitError, KalshiServerError)),
    reraise=True,
)
