"""Refund tests.

Refunds move money like deducts/topups, so the same failure modes apply -- retries,
concurrency, and the balance constraint. These tests concentrate there, plus the
refund-specific rules: full-amount only, at-most-once, and ledger immutability.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

from flask.testing import FlaskClient

from wallet_service.errors import TransactionAlreadyRefundedError
from wallet_service.service import WalletService

ORDER_AMOUNT = 10000  # ₹100 in paise


def _create_wallet(client: FlaskClient, initial: int = 0) -> str:
    resp = client.post("/wallets", json={"initial_balance_paise": initial})
    assert resp.status_code == 201
    return resp.get_json()["id"]


def _topup(client: FlaskClient, wallet_id: str, amount: int = ORDER_AMOUNT):
    return client.post(f"/wallets/{wallet_id}/topup", json={"amount_paise": amount})


def _deduct(client: FlaskClient, wallet_id: str, key: str, amount: int = ORDER_AMOUNT):
    return client.post(
        f"/wallets/{wallet_id}/deduct",
        json={"amount_paise": amount, "reference_id": key},
        headers={"Idempotency-Key": key},
    )


def _refund(
    client: FlaskClient,
    wallet_id: str,
    original_transaction_id: str,
    key: str,
    reason: str | None = None,
):
    body: dict[str, object] = {"original_transaction_id": original_transaction_id}
    if reason is not None:
        body["reason"] = reason
    return client.post(f"/wallets/{wallet_id}/refund", json=body, headers={"Idempotency-Key": key})


def _balance(client: FlaskClient, wallet_id: str) -> int:
    return client.get(f"/wallets/{wallet_id}/balance").get_json()["balance_paise"]


# ----- refund direction ------------------------------------------------------


def test_refunding_a_deduct_credits_the_wallet(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]
    assert _balance(client, wallet_id) == 0

    resp = _refund(client, wallet_id, deduct_tx, "refund-1")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["type"] == "refund"
    assert body["amount_paise"] == ORDER_AMOUNT
    assert body["balance_after_paise"] == ORDER_AMOUNT
    assert body["original_transaction_id"] == deduct_tx
    assert _balance(client, wallet_id) == ORDER_AMOUNT


def test_refunding_a_topup_debits_the_wallet(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client)
    topup_tx = _topup(client, wallet_id, 25000).get_json()["id"]
    assert _balance(client, wallet_id) == 25000

    resp = _refund(client, wallet_id, topup_tx, "refund-1")
    assert resp.status_code == 201
    assert resp.get_json()["amount_paise"] == 25000
    assert resp.get_json()["balance_after_paise"] == 0
    assert _balance(client, wallet_id) == 0


def test_refund_is_full_amount_only(client: FlaskClient) -> None:
    """There is no partial refund: the refund always equals the original amount."""
    wallet_id = _create_wallet(client, initial=30000)
    deduct_tx = _deduct(client, wallet_id, "order-1", amount=30000).get_json()["id"]
    refund = _refund(client, wallet_id, deduct_tx, "refund-1").get_json()
    assert refund["amount_paise"] == 30000


# ----- refund reason ---------------------------------------------------------


def test_refund_persists_optional_reason(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]
    resp = _refund(client, wallet_id, deduct_tx, "refund-1", reason="customer cancelled")
    assert resp.get_json()["reason"] == "customer cancelled"


# ----- idempotency -----------------------------------------------------------


def test_duplicate_refund_request_does_not_refund_twice(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]

    first = _refund(client, wallet_id, deduct_tx, "refund-1")
    retry = _refund(client, wallet_id, deduct_tx, "refund-1")

    assert first.status_code == 201
    assert retry.status_code == 200  # replay
    assert first.get_json()["id"] == retry.get_json()["id"]
    assert _balance(client, wallet_id) == ORDER_AMOUNT


def test_same_key_different_original_is_conflict(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=2 * ORDER_AMOUNT)
    tx_a = _deduct(client, wallet_id, "order-a").get_json()["id"]
    tx_b = _deduct(client, wallet_id, "order-b").get_json()["id"]

    assert _refund(client, wallet_id, tx_a, "refund-1").status_code == 201
    conflict = _refund(client, wallet_id, tx_b, "refund-1")
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "idempotency_conflict"


def test_refund_requires_idempotency_key(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]
    resp = client.post(f"/wallets/{wallet_id}/refund", json={"original_transaction_id": deduct_tx})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"


# ----- at-most-once ----------------------------------------------------------


def test_transaction_cannot_be_refunded_twice(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]

    assert _refund(client, wallet_id, deduct_tx, "refund-1").status_code == 201
    second = _refund(client, wallet_id, deduct_tx, "refund-2")
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "transaction_already_refunded"
    # The single refund must not have been applied a second time.
    assert _balance(client, wallet_id) == ORDER_AMOUNT


def test_a_refund_cannot_be_refunded(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]
    refund_tx = _refund(client, wallet_id, deduct_tx, "refund-1").get_json()["id"]

    resp = _refund(client, wallet_id, refund_tx, "refund-2")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "transaction_not_refundable"


# ----- validation ------------------------------------------------------------


def test_refund_unknown_transaction_returns_404(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    resp = _refund(client, wallet_id, str(uuid.uuid4()), "refund-1")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "transaction_not_found"


def test_refund_transaction_from_another_wallet_returns_404(client: FlaskClient) -> None:
    wallet_a = _create_wallet(client, initial=ORDER_AMOUNT)
    wallet_b = _create_wallet(client, initial=ORDER_AMOUNT)
    tx_a = _deduct(client, wallet_a, "order-1").get_json()["id"]

    resp = _refund(client, wallet_b, tx_a, "refund-1")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "transaction_not_found"


def test_refund_unknown_wallet_returns_404(client: FlaskClient) -> None:
    resp = _refund(client, str(uuid.uuid4()), str(uuid.uuid4()), "refund-1")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "wallet_not_found"


# ----- balance constraint ----------------------------------------------------


def test_refunding_topup_that_would_overdraw_is_rejected(client: FlaskClient) -> None:
    """Top up, spend most of it, then a topup refund cannot drive the balance negative."""
    wallet_id = _create_wallet(client)
    topup_tx = _topup(client, wallet_id, 25000).get_json()["id"]
    _deduct(client, wallet_id, "order-1", amount=20000)  # balance now 5000
    assert _balance(client, wallet_id) == 5000

    resp = _refund(client, wallet_id, topup_tx, "refund-1")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "refund_would_overdraw"
    # Balance must be untouched by the rejected refund.
    assert _balance(client, wallet_id) == 5000


# ----- ledger correctness & immutability -------------------------------------


def test_refund_appends_entry_and_leaves_original_unchanged(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]
    _refund(client, wallet_id, deduct_tx, "refund-1", reason="goodwill")

    entries = client.get(f"/wallets/{wallet_id}/transactions").get_json()["transactions"]
    assert [e["type"] for e in entries] == ["refund", "deduct"]

    refund_entry, original_entry = entries
    assert refund_entry["original_transaction_id"] == deduct_tx
    assert refund_entry["reason"] == "goodwill"
    # The original deduct row is untouched: no reason, no back-link, same amount.
    assert original_entry["id"] == deduct_tx
    assert original_entry["original_transaction_id"] is None
    assert original_entry["reason"] is None
    assert original_entry["amount_paise"] == ORDER_AMOUNT


# ----- concurrency (the important part) --------------------------------------


def test_concurrent_retries_of_same_refund_apply_once(
    service: WalletService, client: FlaskClient
) -> None:
    """The same refund key fired 10x concurrently must refund exactly once."""
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]

    def attempt(_: int) -> None:
        service.refund(wallet_id, deduct_tx, None, "refund-solo")

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(attempt, range(10)))

    assert _balance(client, wallet_id) == ORDER_AMOUNT
    ledger = client.get(f"/wallets/{wallet_id}/transactions").get_json()["transactions"]
    assert len([e for e in ledger if e["type"] == "refund"]) == 1


def test_concurrent_distinct_refunds_of_one_transaction_apply_once(
    service: WalletService, client: FlaskClient
) -> None:
    """Distinct keys racing to refund the SAME transaction: exactly one wins."""
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    deduct_tx = _deduct(client, wallet_id, "order-1").get_json()["id"]

    def attempt(i: int) -> bool:
        try:
            service.refund(wallet_id, deduct_tx, None, f"refund-{i}")
            return True
        except TransactionAlreadyRefundedError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))

    assert sum(results) == 1
    assert _balance(client, wallet_id) == ORDER_AMOUNT
    ledger = client.get(f"/wallets/{wallet_id}/transactions").get_json()["transactions"]
    assert len([e for e in ledger if e["type"] == "refund"]) == 1
