"""
RSA-PSS authentication signing for Kalshi API requests.

Kalshi requires per-request signed headers using RSA-PSS with SHA-256.
Headers must be fresh on every request — never cached.

Signing pattern:
    message = timestamp_ms + METHOD + clean_path
    signature = RSA-PSS sign(message, SHA256)
    header = base64(signature)
"""
from __future__ import annotations

import base64
import time
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def load_private_key(path: str):
    """
    Load an RSA private key from a PEM file.

    Args:
        path: Filesystem path to the PEM-encoded RSA private key.

    Returns:
        RSA private key object (cryptography library).
    """
    with open(path, "rb") as f:
        pem_data = f.read()
    return serialization.load_pem_private_key(pem_data, password=None)


def build_kalshi_headers(
    method: str,
    path: str,
    private_key,
    api_key_id: str,
) -> dict:
    """
    Build Kalshi authentication headers for a REST request.

    Generates fresh headers on every call — never cache the result.
    Query parameters are stripped before signing (Kalshi signs path only).

    Args:
        method:     HTTP method in uppercase (e.g. "GET", "POST").
        path:       Request path, optionally including query string.
        private_key: RSA private key object from load_private_key().
        api_key_id: Kalshi API key ID (UUID string from dashboard).

    Returns:
        Dict with KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP,
        KALSHI-ACCESS-SIGNATURE headers.
    """
    # Strip query string — Kalshi signs only the path component
    parsed = urlparse(path)
    clean_path = parsed.path

    # Timestamp in milliseconds as string
    timestamp_ms = str(int(time.time() * 1000))

    # Message to sign: timestamp + METHOD + path
    message = (timestamp_ms + method.upper() + clean_path).encode("utf-8")

    # RSA-PSS sign with SHA-256
    signature_bytes = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )

    signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")

    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": signature_b64,
    }
