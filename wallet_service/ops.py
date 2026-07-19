"""Operational endpoints: health, metrics, and API docs."""

import time
from typing import Any

from flask import Blueprint, Response, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from .db import engine
from .openapi import OPENAPI_SPEC
from .redis_client import redis_client

ops = Blueprint("ops", __name__)

_STARTED_AT = time.monotonic()

SWAGGER_HTML = """<!DOCTYPE html>
<html>
  <head>
    <title>Wallet Service API</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      window.onload = () => {
        window.ui = SwaggerUIBundle({ url: "/openapi.json", dom_id: "#swagger-ui" });
      };
    </script>
  </body>
</html>"""


@ops.get("/health")
def health() -> tuple[Any, int]:
    """Report dependency health and uptime. Returns 503 if a dependency is down."""
    database_ok = _check_database()
    redis_ok = _check_redis()
    healthy = database_ok and redis_ok
    body = {
        "status": "ok" if healthy else "degraded",
        "database": "connected" if database_ok else "unavailable",
        "redis": "connected" if redis_ok else "unavailable",
        "uptime_seconds": round(time.monotonic() - _STARTED_AT, 3),
    }
    return jsonify(body), (200 if healthy else 503)


@ops.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@ops.get("/docs")
def docs() -> Response:
    return Response(SWAGGER_HTML, mimetype="text/html")


@ops.get("/openapi.json")
def openapi() -> tuple[Any, int]:
    return jsonify(OPENAPI_SPEC), 200


def _check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health check must never raise
        return False


def _check_redis() -> bool:
    try:
        return bool(redis_client.ping())
    except Exception:  # noqa: BLE001 - health check must never raise
        return False
