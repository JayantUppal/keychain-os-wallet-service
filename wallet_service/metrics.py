"""Prometheus metrics. Exposed at GET /metrics."""

from prometheus_client import Counter, Histogram

REQUEST_LATENCY = Histogram(
    "wallet_http_request_latency_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "endpoint", "status"),
)

DEDUCTIONS = Counter(
    "wallet_deductions_total",
    "Deduction attempts by outcome.",
    labelnames=("outcome",),  # success | insufficient_balance | replay
)

TOPUPS = Counter(
    "wallet_topups_total",
    "Topup attempts by outcome.",
    labelnames=("outcome",),  # success | replay
)
