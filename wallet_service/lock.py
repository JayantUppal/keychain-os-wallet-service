"""Per-wallet distributed lock, so concurrent deductions on one wallet serialize."""

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from redis import Redis
from redis.exceptions import LockError

from .config import config
from .errors import LockAcquisitionError


def _lock_name(wallet_id: str) -> str:
    return f"wallet:lock:{wallet_id}"


@contextmanager
def wallet_lock(redis_client: Redis, wallet_id: str) -> Iterator[None]:
    """Acquire the lock for a wallet or raise if it cannot be acquired in time."""
    lock = redis_client.lock(
        _lock_name(wallet_id),
        timeout=config.lock_timeout_seconds,
        blocking_timeout=config.lock_blocking_timeout_seconds,
    )
    if not lock.acquire():
        raise LockAcquisitionError("Wallet is busy, please retry")
    try:
        yield
    finally:
        # If the lock already expired, the DB row lock still protected the write.
        with suppress(LockError):
            lock.release()
