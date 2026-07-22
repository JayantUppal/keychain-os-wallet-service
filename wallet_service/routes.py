"""HTTP controllers. Thin layer: validate input, call the service, shape the response.

No SQL and no business rules live here.
"""

from typing import Any

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ValidationError

from .errors import InvalidRequestError
from .schemas import CreateWalletRequest, DeductRequest, RefundRequest, TopupRequest
from .service import WalletService

api = Blueprint("api", __name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"


def _service() -> WalletService:
    return current_app.extensions["wallet_service"]


def _parse(model: type[BaseModel]) -> BaseModel:
    """Validate the JSON body against a pydantic model."""
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or "body"
        raise InvalidRequestError(f"{field}: {first['msg']}") from exc


def _idempotency_key(body_key: str | None) -> str | None:
    """Header wins over body so proxies/clients can set it uniformly."""
    return request.headers.get(IDEMPOTENCY_HEADER) or body_key


@api.post("/wallets")
def create_wallet() -> tuple[Any, int]:
    data: CreateWalletRequest = _parse(CreateWalletRequest)  # type: ignore[assignment]
    wallet = _service().create_wallet(data.customer_id, data.initial_balance_paise)
    return jsonify(wallet), 201


@api.post("/wallets/<wallet_id>/topup")
def topup(wallet_id: str) -> tuple[Any, int]:
    data: TopupRequest = _parse(TopupRequest)  # type: ignore[assignment]
    result = _service().topup(
        wallet_id, data.amount_paise, data.reference_id, _idempotency_key(data.idempotency_key)
    )
    return jsonify(result.body), result.status_code


@api.post("/wallets/<wallet_id>/deduct")
def deduct(wallet_id: str) -> tuple[Any, int]:
    data: DeductRequest = _parse(DeductRequest)  # type: ignore[assignment]
    key = _idempotency_key(data.idempotency_key)
    if key is None:
        raise InvalidRequestError(
            "An idempotency key is required for deductions "
            f"(send the {IDEMPOTENCY_HEADER} header or 'idempotency_key' in the body)"
        )
    result = _service().deduct(wallet_id, data.amount_paise, data.reference_id, key)
    return jsonify(result.body), result.status_code


@api.post("/wallets/<wallet_id>/refund")
def refund(wallet_id: str) -> tuple[Any, int]:
    data: RefundRequest = _parse(RefundRequest)  # type: ignore[assignment]
    key = _idempotency_key(data.idempotency_key)
    if key is None:
        raise InvalidRequestError(
            "An idempotency key is required for refunds "
            f"(send the {IDEMPOTENCY_HEADER} header or 'idempotency_key' in the body)"
        )
    result = _service().refund(wallet_id, data.original_transaction_id, data.reason, key)
    return jsonify(result.body), result.status_code


@api.get("/wallets/<wallet_id>/balance")
def get_balance(wallet_id: str) -> tuple[Any, int]:
    return jsonify(_service().get_balance(wallet_id)), 200


@api.get("/wallets/<wallet_id>/transactions")
def get_transactions(wallet_id: str) -> tuple[Any, int]:
    return jsonify({"transactions": _service().get_transactions(wallet_id)}), 200
