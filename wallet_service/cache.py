"""Balance cache. Postgres stays the source of truth; this only speeds up reads."""

from typing import cast

from redis import Redis

from .config import config


def _balance_key(wallet_id: str) -> str:
    return f"wallet:balance:{wallet_id}"


def get_cached_balance(redis_client: Redis, wallet_id: str) -> int | None:
    # decode_responses=True means values come back as str; cast for the type checker.
    value = cast("str | None", redis_client.get(_balance_key(wallet_id)))
    return int(value) if value is not None else None


def set_cached_balance(redis_client: Redis, wallet_id: str, balance_paise: int) -> None:
    redis_client.set(_balance_key(wallet_id), balance_paise, ex=config.balance_cache_ttl_seconds)


def invalidate_balance(redis_client: Redis, wallet_id: str) -> None:
    redis_client.delete(_balance_key(wallet_id))
