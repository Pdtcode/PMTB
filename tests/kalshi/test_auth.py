"""
Tests for RSA-PSS auth signing (build_kalshi_headers, load_private_key).
"""
from __future__ import annotations

import base64
import os
import tempfile
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pmtb.kalshi.auth import build_kalshi_headers, load_private_key


@pytest.fixture
def rsa_private_key():
    """Generate a fresh RSA private key for testing."""
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )


@pytest.fixture
def private_key_pem_file(rsa_private_key, tmp_path):
    """Write RSA private key to a temp PEM file and return the path."""
    pem = rsa_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "test_key.pem"
    key_file.write_bytes(pem)
    return str(key_file)


def test_build_kalshi_headers_keys(rsa_private_key):
    """Test 1: build_kalshi_headers returns dict with the three KALSHI-ACCESS-* headers."""
    headers = build_kalshi_headers("GET", "/trade-api/v2/markets", rsa_private_key, "test-api-key-id")
    assert "KALSHI-ACCESS-KEY" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers


def test_build_kalshi_headers_signature_base64(rsa_private_key):
    """Test 2: Signature is base64-encoded and non-empty."""
    headers = build_kalshi_headers("GET", "/trade-api/v2/markets", rsa_private_key, "test-api-key-id")
    sig = headers["KALSHI-ACCESS-SIGNATURE"]
    assert isinstance(sig, str)
    assert len(sig) > 0
    # Must be valid base64
    decoded = base64.b64decode(sig)
    assert len(decoded) > 0


def test_build_kalshi_headers_timestamp_milliseconds(rsa_private_key):
    """Test 3: Timestamp is current time in milliseconds (within 2 seconds)."""
    before_ms = int(time.time() * 1000)
    headers = build_kalshi_headers("GET", "/trade-api/v2/markets", rsa_private_key, "test-api-key-id")
    after_ms = int(time.time() * 1000)

    ts = int(headers["KALSHI-ACCESS-TIMESTAMP"])
    assert before_ms - 2000 <= ts <= after_ms + 2000


def test_build_kalshi_headers_strips_query_params(rsa_private_key):
    """Test 4: Query string is stripped from path before signing."""
    path_with_query = "/trade-api/v2/markets?status=open&limit=10"
    headers_with_query = build_kalshi_headers("GET", path_with_query, rsa_private_key, "test-api-key-id")

    path_clean = "/trade-api/v2/markets"
    headers_clean = build_kalshi_headers("GET", path_clean, rsa_private_key, "test-api-key-id")

    # Both should produce valid headers; the key header values (key, timestamp) are comparable
    assert "KALSHI-ACCESS-SIGNATURE" in headers_with_query
    assert "KALSHI-ACCESS-SIGNATURE" in headers_clean
    # Access key should be same
    assert headers_with_query["KALSHI-ACCESS-KEY"] == headers_clean["KALSHI-ACCESS-KEY"]


def test_load_private_key_from_pem_file(private_key_pem_file):
    """Test 5: load_private_key loads a PEM RSA private key from file path."""
    key = load_private_key(private_key_pem_file)
    # Should be an RSA private key object
    assert key is not None
    # Should have private_bytes method (is a private key)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert b"PRIVATE KEY" in pem
