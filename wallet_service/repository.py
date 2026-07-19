"""Pure database access. No locking, caching, HTTP, or business rules here."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ProcessedRequest, Transaction, Wallet


def create_wallet(session: Session, customer_id: str | None, initial_balance_paise: int) -> Wallet:
    wallet = Wallet(customer_id=customer_id, balance_paise=initial_balance_paise)
    session.add(wallet)
    session.flush()
    return wallet


def get_wallet(session: Session, wallet_id: str) -> Wallet | None:
    return session.get(Wallet, wallet_id)


def get_wallet_for_update(session: Session, wallet_id: str) -> Wallet | None:
    """Fetch the wallet with a row lock (SELECT ... FOR UPDATE)."""
    stmt = select(Wallet).where(Wallet.id == wallet_id).with_for_update()
    return session.scalars(stmt).first()


def add_transaction(
    session: Session,
    wallet_id: str,
    tx_type: str,
    amount_paise: int,
    balance_after_paise: int,
    reference_id: str | None,
) -> Transaction:
    tx = Transaction(
        wallet_id=wallet_id,
        type=tx_type,
        amount_paise=amount_paise,
        balance_after_paise=balance_after_paise,
        reference_id=reference_id,
    )
    session.add(tx)
    session.flush()
    return tx


def list_transactions(session: Session, wallet_id: str) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.wallet_id == wallet_id)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
    )
    return list(session.scalars(stmt))


def get_processed_request(session: Session, idempotency_key: str) -> ProcessedRequest | None:
    return session.get(ProcessedRequest, idempotency_key)


def add_processed_request(
    session: Session,
    idempotency_key: str,
    wallet_id: str,
    endpoint: str,
    request_fingerprint: str,
    response_body: str,
    status_code: int,
) -> ProcessedRequest:
    record = ProcessedRequest(
        idempotency_key=idempotency_key,
        wallet_id=wallet_id,
        endpoint=endpoint,
        request_fingerprint=request_fingerprint,
        response_body=response_body,
        status_code=status_code,
    )
    session.add(record)
    session.flush()
    return record
