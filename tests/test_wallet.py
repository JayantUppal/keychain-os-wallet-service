"""Wallet Service tests.

Most bugs in payment systems come from concurrency and retries, not happy paths,
so that is where the majority of these tests focus.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

from flask.testing import FlaskClient

from wallet_service.errors import InsufficientBalanceError
from wallet_service.service import WalletService

ORDER_AMOUNT = 10000  # ₹100 in paise


def _create_wallet(client: FlaskClient, initial: int = 0) -> str:
    resp = client.post("/wallets", json={"initial_balance_paise": initial})
    assert resp.status_code == 201
    return resp.get_json()["id"]


def _deduct(client: FlaskClient, wallet_id: str, key: str, amount: int = ORDER_AMOUNT):
    return client.post(
        f"/wallets/{wallet_id}/deduct",
        json={"amount_paise": amount, "reference_id": key},
        headers={"Idempotency-Key": key},
    )


# ----- happy path & CRUD -----------------------------------------------------


def test_create_and_get_balance(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=5000)
    resp = client.get(f"/wallets/{wallet_id}/balance")
    assert resp.status_code == 200
    assert resp.get_json()["balance_paise"] == 5000


def test_multiple_topups_accumulate(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client)
    for amount in (10000, 5000, 2500):
        resp = client.post(f"/wallets/{wallet_id}/topup", json={"amount_paise": amount})
        assert resp.status_code == 201
    assert client.get(f"/wallets/{wallet_id}/balance").get_json()["balance_paise"] == 17500


# ----- balance constraint ----------------------------------------------------


def test_deduct_succeeds_with_sufficient_balance(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    resp = _deduct(client, wallet_id, "order-1")
    assert resp.status_code == 201
    assert resp.get_json()["balance_after_paise"] == 0


def test_deduct_fails_with_insufficient_balance(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=5000)  # only ₹50
    resp = _deduct(client, wallet_id, "order-1")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "insufficient_balance"
    # Balance must be untouched.
    assert client.get(f"/wallets/{wallet_id}/balance").get_json()["balance_paise"] == 5000


def test_wallet_never_goes_negative_across_many_orders(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=3 * ORDER_AMOUNT)
    outcomes = [_deduct(client, wallet_id, f"order-{i}").status_code for i in range(5)]
    assert outcomes.count(201) == 3
    assert outcomes.count(422) == 2
    assert client.get(f"/wallets/{wallet_id}/balance").get_json()["balance_paise"] == 0


# ----- idempotency -----------------------------------------------------------


def test_duplicate_request_does_not_deduct_twice(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=2 * ORDER_AMOUNT)
    first = _deduct(client, wallet_id, "order-42")
    retry = _deduct(client, wallet_id, "order-42")

    assert first.status_code == 201
    assert retry.status_code == 200  # replay
    assert first.get_json()["id"] == retry.get_json()["id"]
    assert client.get(f"/wallets/{wallet_id}/balance").get_json()["balance_paise"] == ORDER_AMOUNT


def test_same_key_different_body_is_conflict(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=5 * ORDER_AMOUNT)
    assert _deduct(client, wallet_id, "order-9", amount=ORDER_AMOUNT).status_code == 201
    conflict = _deduct(client, wallet_id, "order-9", amount=ORDER_AMOUNT * 2)
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "idempotency_conflict"


def test_deduct_requires_idempotency_key(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client, initial=ORDER_AMOUNT)
    resp = client.post(f"/wallets/{wallet_id}/deduct", json={"amount_paise": ORDER_AMOUNT})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"


# ----- ledger correctness ----------------------------------------------------


def test_ledger_records_every_movement_with_running_balance(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client)
    client.post(f"/wallets/{wallet_id}/topup", json={"amount_paise": 30000})
    _deduct(client, wallet_id, "order-1")
    _deduct(client, wallet_id, "order-2")

    entries = client.get(f"/wallets/{wallet_id}/transactions").get_json()["transactions"]
    assert len(entries) == 3
    # Newest first: two deducts then the topup.
    assert [e["type"] for e in entries] == ["deduct", "deduct", "topup"]
    assert entries[0]["balance_after_paise"] == 10000
    assert entries[-1]["balance_after_paise"] == 30000


# ----- errors ----------------------------------------------------------------


def test_unknown_wallet_returns_404(client: FlaskClient) -> None:
    resp = client.get(f"/wallets/{uuid.uuid4()}/balance")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "wallet_not_found"


def test_invalid_amount_is_rejected(client: FlaskClient) -> None:
    wallet_id = _create_wallet(client)
    resp = client.post(f"/wallets/{wallet_id}/topup", json={"amount_paise": -5})
    assert resp.status_code == 400


# ----- concurrency (the important part) --------------------------------------


def test_concurrent_deductions_never_oversell(service: WalletService, client: FlaskClient) -> None:
    """20 orders race for a wallet that can only fund 5. Exactly 5 must win."""
    fundable = 5
    wallet_id = _create_wallet(client, initial=fundable * ORDER_AMOUNT)

    def attempt(i: int) -> bool:
        try:
            service.deduct(wallet_id, ORDER_AMOUNT, f"order-{i}", f"order-{i}")
            return True
        except InsufficientBalanceError:
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))

    assert sum(results) == fundable
    assert client.get(f"/wallets/{wallet_id}/balance").get_json()["balance_paise"] == 0


def test_concurrent_retries_of_same_order_deduct_once(
    service: WalletService, client: FlaskClient
) -> None:
    """The same order id sent 10 times concurrently must deduct exactly once."""
    wallet_id = _create_wallet(client, initial=5 * ORDER_AMOUNT)

    def attempt(_: int) -> None:
        service.deduct(wallet_id, ORDER_AMOUNT, "order-solo", "order-solo")

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(attempt, range(10)))

    balance = client.get(f"/wallets/{wallet_id}/balance").get_json()["balance_paise"]
    assert balance == 4 * ORDER_AMOUNT
    ledger = client.get(f"/wallets/{wallet_id}/transactions").get_json()["transactions"]
    assert len([e for e in ledger if e["type"] == "deduct"]) == 1
