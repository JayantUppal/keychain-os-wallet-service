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
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from redis import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import repository as repo
from .cache import get_cached_balance, set_cached_balance
from .errors import (
    IdempotencyConflictError,
    InsufficientBalanceError,
    RefundWouldOverdrawError,
    TransactionAlreadyRefundedError,
    TransactionNotFoundError,
    TransactionNotRefundableError,
    WalletNotFoundError,
)
from .lock import wallet_lock
from .logging_config import get_logger, log_event
from .metrics import DEDUCTIONS, REFUNDS, TOPUPS
from .models import DEDUCT, REFUND, REFUNDABLE_TYPES, TOPUP, ProcessedRequest, Transaction, Wallet

logger = get_logger(__name__)

CREATED_STATUS = 201
REPLAY_STATUS = 200

# Endpoint label recorded on refund idempotency records and fingerprints.
REFUND_ENDPOINT = "refund"

# Timestamps are stored in UTC but surfaced to clients in India Standard Time.
IST = ZoneInfo("Asia/Kolkata")


def _to_ist_isoformat(value: datetime | None) -> str | None:
    """Render a stored timestamp as an ISO-8601 string in IST (+05:30)."""
    if value is None:
        return None
    # A naive value from the DB is UTC; make it aware before converting.
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(IST).isoformat()


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
        "original_transaction_id": tx.original_transaction_id,
        "reason": tx.reason,
        "created_at": _to_ist_isoformat(tx.created_at),
    }


def _fingerprint(endpoint: str, wallet_id: str, tx_type: str, amount_paise: int) -> str:
    raw = f"{endpoint}:{wallet_id}:{tx_type}:{amount_paise}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _refund_fingerprint(endpoint: str, wallet_id: str, original_transaction_id: str) -> str:
    raw = f"{endpoint}:{wallet_id}:{original_transaction_id}"
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

    def refund(
        self,
        wallet_id: str,
        original_transaction_id: str,
        reason: str | None,
        idempotency_key: str | None,
    ) -> WriteResult:
        """Reverse a topup or deduct exactly once, idempotently and atomically."""
        try:
            result = self._apply_refund(wallet_id, original_transaction_id, reason, idempotency_key)
        except TransactionNotFoundError:
            REFUNDS.labels(outcome="not_found").inc()
            raise
        except TransactionNotRefundableError:
            REFUNDS.labels(outcome="not_refundable").inc()
            raise
        except TransactionAlreadyRefundedError:
            REFUNDS.labels(outcome="already_refunded").inc()
            raise
        except RefundWouldOverdrawError:
            REFUNDS.labels(outcome="would_overdraw").inc()
            raise
        REFUNDS.labels(outcome="replay" if result.replayed else "success").inc()
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

    def _apply_refund(
        self,
        wallet_id: str,
        original_transaction_id: str,
        reason: str | None,
        idempotency_key: str | None,
    ) -> WriteResult:
        """Refund write path, serialized by the per-wallet lock (mirrors _apply)."""
        fingerprint = _refund_fingerprint(REFUND_ENDPOINT, wallet_id, original_transaction_id)

        with wallet_lock(self._redis, wallet_id):
            if idempotency_key is not None:
                replay = self._check_idempotency(wallet_id, idempotency_key, fingerprint)
                if replay is not None:
                    return replay

            try:
                result = self._write_refund(
                    wallet_id, original_transaction_id, reason, idempotency_key, fingerprint
                )
            except IntegrityError:
                # Either an idempotency-key race or the unique-refund race for this original.
                replay = self._check_idempotency(wallet_id, idempotency_key, fingerprint)
                if replay is not None:
                    result = replay
                else:
                    self._raise_if_already_refunded(original_transaction_id)
                    raise

        set_cached_balance(self._redis, wallet_id, result.body["balance_after_paise"])
        return result

    def _write_refund(
        self,
        wallet_id: str,
        original_transaction_id: str,
        reason: str | None,
        idempotency_key: str | None,
        fingerprint: str,
    ) -> WriteResult:
        with self._session_factory() as session, session.begin():
            wallet = repo.get_wallet_for_update(session, wallet_id)
            if wallet is None:
                raise WalletNotFoundError(f"Wallet {wallet_id} not found")

            original = self._load_refundable_original(session, wallet_id, original_transaction_id)
            old_balance = wallet.balance_paise
            wallet.balance_paise = self._refunded_balance(wallet, original)

            tx = repo.add_transaction(
                session,
                wallet_id,
                REFUND,
                original.amount_paise,
                wallet.balance_paise,
                original.reference_id,
                original_transaction_id=original.id,
                reason=reason,
            )
            body = _transaction_view(tx)

            if idempotency_key is not None:
                repo.add_processed_request(
                    session,
                    idempotency_key,
                    wallet_id,
                    REFUND_ENDPOINT,
                    fingerprint,
                    json.dumps(body),
                    CREATED_STATUS,
                )

            log_event(
                logger,
                logging.INFO,
                "wallet refund",
                wallet_id=wallet_id,
                transaction_id=tx.id,
                original_transaction_id=original.id,
                original_type=original.type,
                amount_paise=original.amount_paise,
                old_balance=old_balance,
                new_balance=wallet.balance_paise,
                idempotency_key=idempotency_key,
            )
            return WriteResult(body=body, status_code=CREATED_STATUS, replayed=False)

    @staticmethod
    def _load_refundable_original(
        session: Session, wallet_id: str, original_transaction_id: str
    ) -> Transaction:
        """Load the original transaction and assert it can still be refunded."""
        original = repo.get_transaction(session, original_transaction_id)
        if original is None or original.wallet_id != wallet_id:
            raise TransactionNotFoundError(
                f"Transaction {original_transaction_id} not found for wallet {wallet_id}"
            )
        if original.type not in REFUNDABLE_TYPES:
            raise TransactionNotRefundableError(
                f"Transaction {original_transaction_id} of type '{original.type}' "
                "cannot be refunded"
            )
        if repo.get_refund_for_original(session, original_transaction_id) is not None:
            raise TransactionAlreadyRefundedError(
                f"Transaction {original_transaction_id} has already been refunded"
            )
        return original

    @staticmethod
    def _refunded_balance(wallet: Wallet, original: Transaction) -> int:
        """Reverse the original movement: credit a refunded deduct, debit a refunded topup."""
        if original.type == DEDUCT:
            return wallet.balance_paise + original.amount_paise
        if wallet.balance_paise < original.amount_paise:
            raise RefundWouldOverdrawError(
                f"Refunding topup {original.id} needs {original.amount_paise} paise but "
                f"wallet holds {wallet.balance_paise} paise"
            )
        return wallet.balance_paise - original.amount_paise

    def _raise_if_already_refunded(self, original_transaction_id: str) -> None:
        with self._session_factory() as session:
            if repo.get_refund_for_original(session, original_transaction_id) is not None:
                raise TransactionAlreadyRefundedError(
                    f"Transaction {original_transaction_id} has already been refunded"
                )

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
