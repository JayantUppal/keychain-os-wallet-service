"""Data model.

Design:
  - Wallet holds the authoritative balance (fast reads, easy row locking).
  - Transaction is an append-only ledger: one immutable row per money movement.
    We never UPDATE a transaction, only INSERT. This gives us auditability and
    reconciliation, exactly like a real finance system.
  - ProcessedRequest persists the outcome of an idempotent request so a retry
    returns the same response and never applies a second money movement.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

TOPUP = "topup"
DEDUCT = "deduct"
REFUND = "refund"

# The transaction types a refund is allowed to reverse. A refund itself is not refundable.
REFUNDABLE_TYPES = frozenset({TOPUP, DEDUCT})


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Wallet(Base):
    """A customer's wallet. balance_paise is the source of truth for the balance."""

    __tablename__ = "wallets"
    # The database itself refuses to store a negative balance.
    __table_args__ = (CheckConstraint("balance_paise >= 0", name="ck_wallet_balance_non_negative"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    balance_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Transaction(Base):
    """Immutable ledger entry. reference_id links to a business event (e.g. order_id).

    A REFUND entry reverses exactly one earlier TOPUP or DEDUCT. It points back to that
    entry via original_transaction_id. The unique constraint on original_transaction_id lets
    the database itself guarantee a transaction can be refunded at most once.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_transaction_amount_positive"),
        CheckConstraint(
            "balance_after_paise >= 0", name="ck_transaction_balance_after_non_negative"
        ),
        # Postgres treats NULLs as distinct, so non-refund rows (NULL) are unaffected while
        # at most one refund can exist per original transaction.
        UniqueConstraint("original_transaction_id", name="uq_transaction_original_transaction_id"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    wallet_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("wallets.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # topup | deduct | refund
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Set only on REFUND rows: the ledger entry this refund reverses.
    original_transaction_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("transactions.id"), nullable=True
    )
    # Optional, human-supplied justification for a refund.
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProcessedRequest(Base):
    """Idempotency record: the stored outcome of a mutating request."""

    __tablename__ = "processed_requests"

    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    wallet_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("wallets.id"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    # Fingerprint of the request so the same key with a different body is a conflict.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
