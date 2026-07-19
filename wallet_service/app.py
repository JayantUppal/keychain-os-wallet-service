"""Flask application factory: wiring, middleware, and error handling."""

import logging
import time
import uuid
from typing import Any

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from .config import config
from .db import SessionLocal, init_db
from .errors import WalletError
from .logging_config import configure_logging, get_logger, log_event
from .metrics import REQUEST_LATENCY
from .ops import ops
from .redis_client import redis_client
from .routes import api
from .service import WalletService

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def create_app(*, run_init_db: bool = True) -> Flask:
    configure_logging()
    app = Flask(__name__)

    app.extensions["wallet_service"] = WalletService(SessionLocal, redis_client)

    app.register_blueprint(api)
    app.register_blueprint(ops)

    _register_request_lifecycle(app)
    _register_error_handlers(app)

    if run_init_db:
        init_db()

    return app


def _register_request_lifecycle(app: Flask) -> None:
    @app.before_request
    def _start_timer() -> None:
        g.request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        g.start_time = time.perf_counter()

    @app.after_request
    def _log_and_measure(response: Response) -> Response:
        latency = time.perf_counter() - getattr(g, "start_time", time.perf_counter())
        endpoint = request.url_rule.rule if request.url_rule else request.path
        response.headers[REQUEST_ID_HEADER] = getattr(g, "request_id", "")

        REQUEST_LATENCY.labels(
            method=request.method, endpoint=endpoint, status=response.status_code
        ).observe(latency)

        log_event(
            logger,
            logging.INFO,
            "http request",
            request_id=getattr(g, "request_id", None),
            method=request.method,
            path=request.path,
            status=response.status_code,
            latency_ms=round(latency * 1000, 2),
        )
        return response


def _register_error_handlers(app: Flask) -> None:
    def _error(code: str, message: str, status: int) -> tuple[Any, int]:
        return jsonify({"error": {"code": code, "message": message}}), status

    @app.errorhandler(WalletError)
    def _handle_wallet_error(exc: WalletError) -> tuple[Any, int]:
        return _error(exc.code, exc.message, exc.status_code)

    @app.errorhandler(HTTPException)
    def _handle_http_error(exc: HTTPException) -> tuple[Any, int]:
        code = exc.name.lower().replace(" ", "_")
        return _error(code, exc.description or exc.name, exc.code or 500)

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception) -> tuple[Any, int]:
        # Never leak internal details to the client, but log the full stack trace.
        logger.exception(
            "unhandled error",
            extra={"extra_fields": {"request_id": getattr(g, "request_id", None)}},
        )
        return _error("internal_error", "An unexpected error occurred", 500)


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=config.flask_port)
