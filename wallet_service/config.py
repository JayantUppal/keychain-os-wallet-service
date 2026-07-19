"""Environment-driven configuration."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Config:
    """All tunables in one place. Values come from the environment."""

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://wallet:wallet@localhost:5432/wallet",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Default deduction amount used by the Order Service stub (10000 paise = ₹100).
    deduct_amount_paise: int = _env_int("DEDUCT_AMOUNT_PAISE", 10000)

    # How long a lock is held before it auto-expires, and how long we wait to get it.
    lock_timeout_seconds: int = _env_int("LOCK_TIMEOUT_SECONDS", 10)
    lock_blocking_timeout_seconds: int = _env_int("LOCK_BLOCKING_TIMEOUT_SECONDS", 5)

    balance_cache_ttl_seconds: int = _env_int("BALANCE_CACHE_TTL_SECONDS", 30)

    flask_port: int = _env_int("FLASK_PORT", 5000)


config = Config()
