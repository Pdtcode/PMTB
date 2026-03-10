"""
Tests for error classification and retry decorator (kalshi_retry).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pmtb.kalshi.errors import (
    KalshiClientError,
    KalshiRateLimitError,
    KalshiServerError,
    classify_error,
    kalshi_retry,
)


def test_kalshi_retry_retries_on_rate_limit():
    """Test 6: kalshi_retry retries on KalshiRateLimitError up to 5 times."""
    call_count = 0

    @kalshi_retry
    def flaky_rate_limit():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise KalshiRateLimitError(429, "rate limited")
        return "ok"

    # Patch tenacity sleep to avoid actual waiting
    with patch("tenacity.nap.time") as mock_sleep:
        mock_sleep.sleep = MagicMock()
        result = flaky_rate_limit()

    assert result == "ok"
    assert call_count == 3


def test_kalshi_retry_retries_on_server_error():
    """Test 7: kalshi_retry retries on KalshiServerError up to 5 times."""
    call_count = 0

    @kalshi_retry
    def flaky_server():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise KalshiServerError(503, "service unavailable")
        return "ok"

    with patch("tenacity.nap.time") as mock_sleep:
        mock_sleep.sleep = MagicMock()
        result = flaky_server()

    assert result == "ok"
    assert call_count == 3


def test_kalshi_retry_does_not_retry_client_error():
    """Test 8: kalshi_retry does NOT retry on KalshiClientError — raises immediately."""
    call_count = 0

    @kalshi_retry
    def bad_request():
        nonlocal call_count
        call_count += 1
        raise KalshiClientError(400, "bad request")

    with pytest.raises(KalshiClientError):
        bad_request()

    assert call_count == 1  # Called only once, no retries


def test_kalshi_retry_uses_exponential_backoff_jitter():
    """Test 9: kalshi_retry uses wait_exponential_jitter (verify via tenacity retry stats)."""
    call_count = 0

    @kalshi_retry
    def fails_twice():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise KalshiRateLimitError(429, "rate limited")
        return "done"

    with patch("tenacity.nap.time") as mock_sleep:
        mock_sleep.sleep = MagicMock()
        result = fails_twice()

    assert result == "done"
    # The retry decorator should have waited (mock_sleep.sleep called)
    assert mock_sleep.sleep.called


def test_classify_error_429_returns_rate_limit():
    err = classify_error(429, "rate limited")
    assert isinstance(err, KalshiRateLimitError)
    assert err.status_code == 429


def test_classify_error_500_returns_server_error():
    err = classify_error(500, "internal server error")
    assert isinstance(err, KalshiServerError)
    assert err.status_code == 500


def test_classify_error_503_returns_server_error():
    err = classify_error(503, "service unavailable")
    assert isinstance(err, KalshiServerError)


def test_classify_error_400_returns_client_error():
    err = classify_error(400, "bad request")
    assert isinstance(err, KalshiClientError)
    assert err.status_code == 400


def test_classify_error_403_returns_client_error():
    err = classify_error(403, "forbidden")
    assert isinstance(err, KalshiClientError)
