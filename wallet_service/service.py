"""Business logic: create, top up, deduct, read balance and history.

Safety model for money movement (defense in depth):
  1. Redis per-wallet lock  -> serializes concurrent writers across processes,
                               and keeps them off the same DB row (less contention).
  2. SELECT ... FOR UPDATE   -> the real correctness guarantee: serializes writers
                               at the DB row level inside one transaction.
  3. ProcessedRequest table  -> a retried request applies at most once and returns
                               the original response.
  4. CHECK (balance >= 0)    -> the database refuses to store a negative balance,
                               even if application logic has a bug.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from redis import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from . import repository as repo
from .cache import get_cached_balance, set_cached_balance
from .errors import IdempotencyConflictError, InsufficientBalanceError, WalletNotFoundError
from .lock import wallet_lock
from .logging_config import get_logger, log_event
from .metrics import DEDUCTIONS, TOPUPS
from .models import DEDUCT, TOPUP, ProcessedRequest, Transaction, Wallet

logger = get_logger(__name__)

CREATED_STATUS = 201
REPLAY_STATUS = 200


@dataclass
class WriteResult:
    """Outcome of a mutating operation, ready for the HTTP layer."""

    body: dict[str, Any]
    status_code: int
    replayed: bool


def _rupees(paise: int) -> str:
    """Format paise as a rupee string without floating-point rounding."""
    return f"{paise // 100}.{paise % 100:02d}"


def _wallet_view(wallet: Wallet) -> dict[str, Any]:
    return {
        "id": wallet.id,
        "customer_id": wallet.customer_id,
        "balance_paise": wallet.balance_paise,
        "balance_rupees": _rupees(wallet.balance_paise),
    }


def _transaction_view(tx: Transaction) -> dict[str, Any]:
    return {
        "id": tx.id,
        "wallet_id": tx.wallet_id,
        "type": tx.type,
        "amount_paise": tx.amount_paise,
        "balance_after_paise": tx.balance_after_paise,
        "reference_id": tx.reference_id,
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
    }


def _fingerprint(endpoint: str, wallet_id: str, tx_type: str, amount_paise: int) -> str:
    raw = f"{endpoint}:{wallet_id}:{tx_type}:{amount_paise}"
    return hashlib.sha256(raw.encode()).hexdigest()


class WalletService:
    def __init__(self, session_factory: sessionmaker, redis_client: Redis) -> None:
        self._session_factory = session_factory
        self._redis = redis_client

    # ----- reads -------------------------------------------------------------

    def create_wallet(self, customer_id: str | None, initial_balance_paise: int) -> dict[str, Any]:
        with self._session_factory() as session, session.begin():
            wallet = repo.create_wallet(session, customer_id, initial_balance_paise)
            view = _wallet_view(wallet)
        set_cached_balance(self._redis, view["id"], view["balance_paise"])
        log_event(logger, logging.INFO, "wallet created", wallet_id=view["id"])
        return view

    def get_balance(self, wallet_id: str) -> dict[str, Any]:
        cached = get_cached_balance(self._redis, wallet_id)
        if cached is not None:
            return self._balance_view(wallet_id, cached)

        with self._session_factory() as session:
            wallet = repo.get_wallet(session, wallet_id)
            if wallet is None:
                raise WalletNotFoundError(f"Wallet {wallet_id} not found")
            balance = wallet.balance_paise
        set_cached_balance(self._redis, wallet_id, balance)
        return self._balance_view(wallet_id, balance)

    def get_transactions(self, wallet_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            if repo.get_wallet(session, wallet_id) is None:
                raise WalletNotFoundError(f"Wallet {wallet_id} not found")
            return [_transaction_view(tx) for tx in repo.list_transactions(session, wallet_id)]

    @staticmethod
    def _balance_view(wallet_id: str, balance: int) -> dict[str, Any]:
        return {
            "wallet_id": wallet_id,
            "balance_paise": balance,
            "balance_rupees": _rupees(balance),
        }

    # ----- writes ------------------------------------------------------------

    def topup(
        self,
        wallet_id: str,
        amount_paise: int,
        reference_id: str | None,
        idempotency_key: str | None,
    ) -> WriteResult:
        result = self._apply(wallet_id, TOPUP, amount_paise, reference_id, idempotency_key, "topup")
        TOPUPS.labels(outcome="replay" if result.replayed else "success").inc()
        return result

    def deduct(
        self,
        wallet_id: str,
        amount_paise: int,
        reference_id: str | None,
        idempotency_key: str | None,
    ) -> WriteResult:
        try:
            result = self._apply(
                wallet_id, DEDUCT, amount_paise, reference_id, idempotency_key, "deduct"
            )
        except InsufficientBalanceError:
            DEDUCTIONS.labels(outcome="insufficient_balance").inc()
            raise
        DEDUCTIONS.labels(outcome="replay" if result.replayed else "success").inc()
        return result

    def _apply(
        self,
        wallet_id: str,
        tx_type: str,
        amount_paise: int,
        reference_id: str | None,
        idempotency_key: str | None,
        endpoint: str,
    ) -> WriteResult:
        """Shared write path, serialized by the per-wallet lock."""
        fingerprint = _fingerprint(endpoint, wallet_id, tx_type, amount_paise)

        with wallet_lock(self._redis, wallet_id):
            if idempotency_key is not None:
                replay = self._check_idempotency(wallet_id, idempotency_key, fingerprint)
                if replay is not None:
                    return replay

            try:
                result = self._write(
                    wallet_id,
                    tx_type,
                    amount_paise,
                    reference_id,
                    idempotency_key,
                    endpoint,
                    fingerprint,
                )
            except IntegrityError:
                # Lost a race on the idempotency key; return the stored response.
                replay = self._check_idempotency(wallet_id, idempotency_key, fingerprint)
                if replay is None:
                    raise
                result = replay

        set_cached_balance(self._redis, wallet_id, result.body["balance_after_paise"])
        return result

    def _write(
        self,
        wallet_id: str,
        tx_type: str,
        amount_paise: int,
        reference_id: str | None,
        idempotency_key: str | None,
        endpoint: str,
        fingerprint: str,
    ) -> WriteResult:
        with self._session_factory() as session, session.begin():
            wallet = repo.get_wallet_for_update(session, wallet_id)
            if wallet is None:
                raise WalletNotFoundError(f"Wallet {wallet_id} not found")

            old_balance = wallet.balance_paise
            if tx_type == DEDUCT:
                if wallet.balance_paise < amount_paise:
                    raise InsufficientBalanceError(
                        f"Insufficient balance: have {wallet.balance_paise} paise, "
                        f"need {amount_paise} paise"
                    )
                wallet.balance_paise -= amount_paise
            else:
                wallet.balance_paise += amount_paise

            tx = repo.add_transaction(
                session, wallet_id, tx_type, amount_paise, wallet.balance_paise, reference_id
            )
            body = _transaction_view(tx)

            if idempotency_key is not None:
                repo.add_processed_request(
                    session,
                    idempotency_key,
                    wallet_id,
                    endpoint,
                    fingerprint,
                    json.dumps(body),
                    CREATED_STATUS,
                )

            log_event(
                logger,
                logging.INFO,
                f"wallet {tx_type}",
                wallet_id=wallet_id,
                transaction_id=tx.id,
                reference_id=reference_id,
                amount_paise=amount_paise,
                old_balance=old_balance,
                new_balance=wallet.balance_paise,
                idempotency_key=idempotency_key,
            )
            return WriteResult(body=body, status_code=CREATED_STATUS, replayed=False)

    def _check_idempotency(
        self, wallet_id: str, idempotency_key: str | None, fingerprint: str
    ) -> WriteResult | None:
        if idempotency_key is None:
            return None
        with self._session_factory() as session:
            record = repo.get_processed_request(session, idempotency_key)
            if record is None:
                return None
            self._assert_same_request(record, wallet_id, fingerprint)
            log_event(
                logger,
                logging.INFO,
                "idempotent replay",
                wallet_id=wallet_id,
                idempotency_key=idempotency_key,
            )
            return WriteResult(
                body=json.loads(record.response_body),
                status_code=REPLAY_STATUS,
                replayed=True,
            )

    @staticmethod
    def _assert_same_request(record: ProcessedRequest, wallet_id: str, fingerprint: str) -> None:
        if record.wallet_id != wallet_id or record.request_fingerprint != fingerprint:
            raise IdempotencyConflictError("Idempotency key already used for a different request")
