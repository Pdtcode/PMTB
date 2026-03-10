"""
Prometheus metrics registry for PMTB.

Defines all application metrics. Disables macOS-incompatible default process
collectors to avoid /proc errors on non-Linux platforms.

Usage:
    from pmtb.metrics import CYCLE_COUNT, CYCLE_LATENCY, start_metrics_server

    CYCLE_COUNT.inc()

    with CYCLE_LATENCY.time():
        await run_scan_cycle()

    # Start HTTP /metrics endpoint (optional, runs in background thread)
    start_metrics_server(port=9090)
"""

from __future__ import annotations

import platform
import threading

import prometheus_client
from prometheus_client import Counter, Gauge, Histogram

# Disable default process/platform collectors that read /proc/
# These fail on macOS and are not useful for application-level metrics
try:
    prometheus_client.REGISTRY.unregister(prometheus_client.PROCESS_COLLECTOR)
except Exception:
    pass  # Already unregistered or not present

try:
    prometheus_client.REGISTRY.unregister(prometheus_client.PLATFORM_COLLECTOR)
except Exception:
    pass  # Already unregistered or not present

try:
    prometheus_client.REGISTRY.unregister(prometheus_client.GC_COLLECTOR)
except Exception:
    pass  # Already unregistered or not present


# --- Core trading cycle metrics ---

CYCLE_COUNT = Counter(
    "pmtb_scan_cycles_total",
    "Total number of completed market scan cycles",
)

CYCLE_LATENCY = Histogram(
    "pmtb_scan_cycle_duration_seconds",
    "Time taken to complete a full market scan cycle",
    buckets=[1, 5, 10, 30, 60, 120, 300],
)

# --- Error tracking ---

ERROR_COUNT = Counter(
    "pmtb_errors_total",
    "Total number of errors by type",
    labelnames=["error_type"],
)

# --- Position tracking ---

OPEN_POSITIONS = Gauge(
    "pmtb_open_positions",
    "Current number of open positions",
)

# --- API call tracking ---

API_CALLS = Counter(
    "pmtb_api_calls_total",
    "Total number of API calls by endpoint and status",
    labelnames=["endpoint", "status"],
)


def start_metrics_server(port: int = 9090) -> None:
    """
    Start Prometheus HTTP metrics server in a background daemon thread.

    The server exposes /metrics endpoint for Prometheus scraping.
    Runs as a daemon so it doesn't prevent process shutdown.

    Args:
        port: TCP port to listen on (default 9090).
    """
    def _serve():
        prometheus_client.start_http_server(port)

    thread = threading.Thread(target=_serve, daemon=True, name="metrics-server")
    thread.start()
