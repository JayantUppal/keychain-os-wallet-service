"""Order Service stub.

Simulates how the Order Service integrates with the Wallet Service:
  1. Create a wallet and top it up.
  2. Place an order -> deduct ₹100 with an idempotency key (the order id).
  3. Retry the SAME deduction (network retry) -> no second deduction.
  4. Place orders until the balance runs out -> a clean 422 error.

Run the service first (make run), then: python order_service_stub.py
"""

import os
import uuid

import requests

BASE_URL = os.getenv("WALLET_BASE_URL", "http://localhost:5000")
ORDER_AMOUNT_PAISE = int(os.getenv("DEDUCT_AMOUNT_PAISE", "10000"))  # ₹100
TIMEOUT_SECONDS = 5


def _post(path: str, body: dict, headers: dict | None = None) -> requests.Response:
    return requests.post(
        f"{BASE_URL}{path}", json=body, headers=headers or {}, timeout=TIMEOUT_SECONDS
    )


def _get(path: str) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT_SECONDS)


def place_order(wallet_id: str, order_id: str) -> requests.Response:
    """Deduct for one order. order_id is the idempotency key, so retries are safe."""
    return _post(
        f"/wallets/{wallet_id}/deduct",
        {"amount_paise": ORDER_AMOUNT_PAISE, "reference_id": order_id},
        headers={"Idempotency-Key": order_id},
    )


def main() -> None:
    wallet = _post("/wallets", {"customer_id": "acme-logistics"}).json()
    wallet_id = wallet["id"]
    print(f"Created wallet {wallet_id}")

    _post(f"/wallets/{wallet_id}/topup", {"amount_paise": 25000})  # ₹250
    print(f"Topped up. Balance: {_get(f'/wallets/{wallet_id}/balance').json()}")

    order_id = f"order-{uuid.uuid4()}"
    first = place_order(wallet_id, order_id)
    print(f"Order placed [{first.status_code}]: {first.json()}")

    retry = place_order(wallet_id, order_id)
    print(f"Same order retried [{retry.status_code}] (should NOT deduct again): {retry.json()}")

    print(f"Balance after 1 order: {_get(f'/wallets/{wallet_id}/balance').json()}")

    # Drain the wallet to show the balance constraint.
    for _ in range(3):
        resp = place_order(wallet_id, f"order-{uuid.uuid4()}")
        print(f"Order attempt [{resp.status_code}]: {resp.json()}")

    print(f"Final balance: {_get(f'/wallets/{wallet_id}/balance').json()}")
    print(f"Ledger: {_get(f'/wallets/{wallet_id}/transactions').json()}")


if __name__ == "__main__":
    main()
