"""Test fixtures.

These are integration tests: they run against the real Postgres and Redis started by
`docker compose up`. We use real infrastructure on purpose, because the behaviour we
care about most -- row locking and idempotency -- cannot be tested against a fake.
"""

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import text

from wallet_service.app import create_app
from wallet_service.db import engine
from wallet_service.redis_client import redis_client
from wallet_service.service import WalletService

_TABLES = ("processed_requests", "transactions", "wallets")


@pytest.fixture(scope="session")
def app() -> Flask:
    return create_app(run_init_db=True)


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Reset Postgres tables and Redis before every test for isolation."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    redis_client.flushdb()
    yield


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def service(app: Flask) -> WalletService:
    return app.extensions["wallet_service"]
